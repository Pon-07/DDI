import os
import tempfile
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.audit.ledger import AuditLedger
from app.main import app
from app.dependencies import get_audit_ledger
from database.database import Base, get_db
from engine.auth.dependencies import get_current_user
from engine.auth.models import AuthBase, User
from models import Medication, Order, Patient, SubstitutionRequest
from tests.test_client import LocalTestClient


class TestSubstitutionWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "workflow.sqlite")
        self.audit_path = os.path.join(self.temp_dir.name, "audit.sqlite")
        self.engine = self._make_engine()
        Base.metadata.create_all(bind=self.engine)
        AuthBase.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()

        self.patient = Patient(patient_identifier="DEMO-PT-002", name="Workflow Test Patient", sex="F")
        self.other_patient = Patient(patient_identifier="DEMO-PT-099", name="Other Test Patient", sex="M")
        self.db.add_all([self.patient, self.other_patient])
        self.db.flush()
        self.medication = Medication(patient_id=self.patient.id, drug_name="Enoxaparin Sodium", status="active")
        self.other_medication = Medication(patient_id=self.other_patient.id, drug_name="Other Medicine", status="active")
        self.db.add_all([self.medication, self.other_medication])
        self.db.flush()
        self.order = Order(
            patient_id=self.patient.id,
            medication_id=self.medication.id,
            drug_name="Enoxaparin Sodium (Lovenox)",
            dose="80",
            dose_unit="mg",
            status="pending",
        )
        self.other_order = Order(
            patient_id=self.other_patient.id,
            medication_id=self.other_medication.id,
            drug_name="Other Medicine",
            dose="10",
            dose_unit="mg",
            status="pending",
        )
        self.pharmacist = User(phone_number="test-pharm", full_name="Test Pharmacist", role="Clinical Pharmacist")
        self.other_pharmacist = User(phone_number="test-pharm-2", full_name="Other Pharmacist", role="Clinical Pharmacist")
        self.doctor = User(phone_number="test-doctor", full_name="Test Doctor", role="Doctor")
        self.nurse = User(phone_number="test-nurse", full_name="Test Nurse", role="Nurse")
        self.db.add_all([self.order, self.other_order, self.pharmacist, self.other_pharmacist, self.doctor, self.nurse])
        self.db.commit()

        self.audit_ledger = AuditLedger(db_path=self.audit_path)
        self.actor = self.pharmacist
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_audit_ledger] = lambda: self.audit_ledger
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.client = LocalTestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _make_engine(self):
        return create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False},
        )

    def _payload(self, **overrides):
        payload = {
            "patient_id": self.patient.id,
            "order_id": self.order.id,
            "medication_id": self.medication.id,
            "idempotency_key": "workflow-idempotency-key-0001",
            "suggested_brand": "Alternative Brand",
            "reason": "Prescribed brand unavailable.",
            "pharmacist_notes": "Requesting prescriber review.",
        }
        payload.update(overrides)
        return payload

    def _submit(self, **overrides):
        self.actor = self.pharmacist
        return self.client.post(
            "/api/dashboard/pharmacist/substitution-request",
            json=self._payload(**overrides),
        )

    def _decide(self, request_id, decision):
        self.actor = self.doctor
        return self.client.post(
            "/api/dashboard/doctor/substitution-decision",
            json={"request_id": request_id, "decision": decision},
        )

    def _reopen_database_session(self):
        self.db.close()
        self.engine.dispose()
        self.engine = self._make_engine()
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()

    def test_submission_persists_and_doctor_dashboard_reads_it_after_reopen(self):
        response = self._submit()
        self.assertEqual(response.status_code, 200, response.text)
        request_id = response.json()["request_id"]
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(self.db.query(SubstitutionRequest).count(), 1)

        # Reopen the file-backed synthetic database to exercise persistence across process/session restart.
        self._reopen_database_session()
        self.actor = self.doctor
        dashboard = self.client.get("/api/dashboard/doctor")
        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        requests = dashboard.json()["substitution_requests"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["request_id"], request_id)
        self.assertEqual(requests[0]["patient_identifier"], "DEMO-PT-002")
        self.assertEqual(requests[0]["suggested_brand"], "Alternative Brand")
        self.assertEqual(requests[0]["status"], "pending")
        # Submission records a request only; the underlying prescription stays unchanged.
        self.assertEqual(self.db.get(Order, self.order.id).drug_name, "Enoxaparin Sodium (Lovenox)")

    def test_doctor_approval_and_rejection_update_pharmacist_status(self):
        created = self._submit()
        request_id = created.json()["request_id"]
        approved = self._decide(request_id, "APPROVED")
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["status"], "approved")

        self.actor = self.pharmacist
        dashboard = self.client.get("/api/dashboard/pharmacist")
        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        queue_item = next(item for item in dashboard.json()["availability_queue"] if item["order_id"] == self.order.id)
        self.assertEqual(queue_item["substitution_request_id"], request_id)
        self.assertEqual(queue_item["substitution_request_status"], "approved")
        self.assertEqual(queue_item["substitution_status"], "Substitution Approved (Ready to Dispense)")

        # A completed request cannot be decided again; a distinct new request can then be rejected.
        second = self._submit(idempotency_key="workflow-idempotency-key-0002")
        self.assertEqual(second.status_code, 200, second.text)
        rejected = self._decide(second.json()["request_id"], "REJECTED")
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()["status"], "rejected")
        repeated = self._decide(second.json()["request_id"], "APPROVED")
        self.assertEqual(repeated.status_code, 409)

        self.actor = self.pharmacist
        refreshed = self.client.get("/api/dashboard/pharmacist")
        queue_item = next(item for item in refreshed.json()["availability_queue"] if item["order_id"] == self.order.id)
        self.assertEqual(queue_item["substitution_request_status"], "rejected")
        self.assertFalse(queue_item["is_ready_for_dispensing"])

    def test_duplicate_submission_is_idempotent_and_only_one_pending_request_exists(self):
        first = self._submit()
        second = self._submit()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["request_id"], second.json()["request_id"])
        self.assertEqual(self.db.query(SubstitutionRequest).count(), 1)
        self.assertEqual(len([e for e in self.audit_ledger.get_events() if e.event_type == "PHARMACY_SUBSTITUTION_REQUESTED"]), 1)

        different_key = self._submit(idempotency_key="workflow-idempotency-key-0003", suggested_brand="Different Brand")
        self.assertEqual(different_key.status_code, 409)
        changed_reason = self._submit(reason="A different reason for the same pending request.")
        self.assertEqual(changed_reason.status_code, 409)
        self.assertEqual(self.db.query(SubstitutionRequest).count(), 1)

    def test_invalid_references_and_missing_fields_are_rejected(self):
        missing = self.client.post("/api/dashboard/pharmacist/substitution-request", json={})
        self.assertEqual(missing.status_code, 422)
        whitespace_reason = self._submit(reason="   ")
        self.assertEqual(whitespace_reason.status_code, 422)

        mismatched_patient = self._submit(patient_id=self.other_patient.id)
        self.assertEqual(mismatched_patient.status_code, 422)

        mismatched_medication = self._submit(medication_id=self.other_medication.id)
        self.assertEqual(mismatched_medication.status_code, 422)

        unknown_order = self._submit(order_id=999999)
        self.assertEqual(unknown_order.status_code, 404)
        self.assertEqual(self.db.query(SubstitutionRequest).count(), 0)

    def test_role_authorization_self_decision_and_pharmacist_patient_scope(self):
        created = self._submit()
        request_id = created.json()["request_id"]

        self.actor = self.pharmacist
        forbidden_doctor_view = self.client.get("/api/dashboard/doctor")
        self.assertEqual(forbidden_doctor_view.status_code, 403)
        forbidden_decision = self.client.post(
            "/api/dashboard/doctor/substitution-decision",
            json={"request_id": request_id, "decision": "APPROVED"},
        )
        self.assertEqual(forbidden_decision.status_code, 403)

        self.actor = self.nurse
        forbidden_submit = self._submit()
        self.assertEqual(forbidden_submit.status_code, 403)

        self.actor = self.pharmacist
        self.assertEqual(self.client.get("/api/dashboard/pharmacist").status_code, 200)
        self.actor = self.other_pharmacist
        other_dashboard = self.client.get("/api/dashboard/pharmacist")
        self.assertEqual(other_dashboard.status_code, 200)
        other_item = next(item for item in other_dashboard.json()["availability_queue"] if item["order_id"] == self.order.id)
        self.assertIsNone(other_item["substitution_request_id"])
        self.assertNotEqual(other_item["substitution_request_status"], "pending")

        # Changing roles on the same account does not permit a pharmacist to decide their own request.
        self.pharmacist.role = "Doctor"
        self.actor = self.pharmacist
        self.db.commit()
        self.assertEqual(self._decide(request_id, "APPROVED").status_code, 403)

    def test_missing_authentication_is_rejected(self):
        app.dependency_overrides.pop(get_current_user, None)
        response = self.client.get("/api/dashboard/doctor")
        self.assertEqual(response.status_code, 401)
        history_response = self.client.get("/api/dashboard/patient-history/DEMO-PT-002")
        self.assertEqual(history_response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
