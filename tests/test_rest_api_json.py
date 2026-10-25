"""
Comprehensive test suite for REST API JSON architecture, CORS configuration,
error handling, and medication substitution workflow endpoints.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.audit.ledger import AuditLedger
from app.dependencies import get_audit_ledger
from app.main import app
from database.database import Base, get_db
from engine.auth.dependencies import get_current_user
from engine.auth.models import AuthBase, User
from models.models import Lab, Medication, Order, Patient, SubstitutionRequest


class LocalTestClient(TestClient):
    def __init__(self, target_app):
        super().__init__(target_app, raise_server_exceptions=False)


class TestRestApiJsonMigration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_api.db")
        self.audit_path = os.path.join(self.temp_dir.name, "test_audit.db")

        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        AuthBase.metadata.create_all(bind=self.engine)
        SubstitutionRequest.__table__.create(bind=self.engine, checkfirst=True)

        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()

        self.patient = Patient(patient_identifier="DEMO-PT-002", name="Robert Chen", sex="M")
        self.other_patient = Patient(patient_identifier="DEMO-PT-999", name="Other Patient", sex="F")
        self.db.add_all([self.patient, self.other_patient])
        self.db.commit()

        self.medication = Medication(
            patient_id=self.patient.id,
            drug_name="enoxaparin",
            dose="40",
            dose_unit="mg",
            status="active",
        )
        self.other_medication = Medication(
            patient_id=self.other_patient.id,
            drug_name="warfarin",
            dose="5",
            dose_unit="mg",
            status="active",
        )
        self.db.add_all([self.medication, self.other_medication])
        self.db.commit()

        self.order = Order(
            patient_id=self.patient.id,
            medication_id=self.medication.id,
            drug_name="Enoxaparin Sodium (Lovenox)",
            dose="40",
            dose_unit="mg",
            route="subcutaneous",
            frequency="daily",
            status="active",
        )
        self.pharmacist = User(
            phone_number="rest-pharm",
            full_name="REST Pharmacist",
            role="Clinical Pharmacist",
        )
        self.doctor = User(
            phone_number="rest-doctor",
            full_name="REST Doctor",
            role="Doctor",
        )
        self.nurse = User(
            phone_number="rest-nurse",
            full_name="REST Nurse",
            role="Nurse",
        )
        self.admin = User(
            phone_number="rest-admin",
            full_name="REST Admin",
            role="Admin",
        )
        self.db.add_all([self.order, self.pharmacist, self.doctor, self.nurse, self.admin])
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

    def test_root_returns_json_api_index(self):
        """GET / must return JSON metadata with platform status and API catalog."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "application/json")
        data = res.json()
        self.assertEqual(data["name"], "AEGIS Rx")
        self.assertEqual(data["status"], "online")
        self.assertIn("docs_url", data)
        self.assertIn("openapi_url", data)
        self.assertIn("endpoints", data)
        self.assertEqual(data["endpoints"]["substitutions"], "/api/substitutions")
        self.assertEqual(data["endpoints"]["legacy_console"], "/app")

    def test_health_and_status_endpoints_return_json(self):
        """Health endpoints must return structured JSON status without exposing secrets."""
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})

        status_res = self.client.get("/status")
        self.assertEqual(status_res.status_code, 200)
        status_data = status_res.json()
        self.assertEqual(status_data["status"], "ok")
        self.assertTrue(status_data["offline_capable"])
        self.assertEqual(status_data["database"], "connected")
        self.assertIn("audit_chain_valid", status_data)

    def test_openapi_schema_is_available_and_valid(self):
        """FastAPI OpenAPI specification must be accessible as JSON."""
        res = self.client.get("/openapi.json")
        self.assertEqual(res.status_code, 200)
        spec = res.json()
        self.assertEqual(spec["info"]["title"], "AEGIS Rx")
        self.assertIn("/api/substitutions", spec["paths"])
        self.assertIn("/api/substitutions/request", spec["paths"])
        self.assertIn("/api/substitutions/decision", spec["paths"])

    def test_cors_headers_for_trusted_origins(self):
        """CORS middleware must allow trusted local origins and not use wildcard with credentials."""
        headers = {
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        }
        res = self.client.options("/api/substitutions/request", headers=headers)
        self.assertEqual(res.headers.get("access-control-allow-origin"), "http://localhost:3000")
        self.assertEqual(res.headers.get("access-control-allow-credentials"), "true")

    def test_substitution_workflow_end_to_end_rest_api(self):
        """Test complete submission, listing, detail, decision, and history via /api/substitutions."""
        # 1. Pharmacist submits a brand substitution request
        self.actor = self.pharmacist
        payload = {
            "patient_id": self.patient.id,
            "order_id": self.order.id,
            "medication_id": self.medication.id,
            "idempotency_key": "rest-idempotency-key-0001",
            "suggested_brand": "Heparin Alternative Brand",
            "reason": "Prescribed formulation unavailable at hospital pharmacy.",
            "pharmacist_notes": "Urgent prescriber review requested.",
        }
        create_res = self.client.post("/api/substitutions/request", json=payload)
        self.assertEqual(create_res.status_code, 200, create_res.text)
        created_data = create_res.json()
        request_id = created_data["request_id"]
        self.assertEqual(created_data["status"], "pending")
        self.assertIsNotNone(created_data["audit_event_id"])
        self.assertIsNotNone(created_data["audit_hash"])

        # 2. Idempotent repeated submission
        duplicate_res = self.client.post("/api/substitutions/request", json=payload)
        self.assertEqual(duplicate_res.status_code, 200)
        self.assertEqual(duplicate_res.json()["request_id"], request_id)

        # 3. Retrieve single request details via GET /api/substitutions/{id}
        detail_res = self.client.get(f"/api/substitutions/{request_id}")
        self.assertEqual(detail_res.status_code, 200)
        detail = detail_res.json()
        self.assertEqual(detail["request_id"], request_id)
        self.assertEqual(detail["case_id"], "DEMO-PT-002")
        self.assertEqual(detail["suggested_brand"], "Heparin Alternative Brand")
        self.assertEqual(detail["status"], "pending")

        # 4. List substitution requests via GET /api/substitutions with filtering
        list_res = self.client.get(f"/api/substitutions?patient_id={self.patient.id}&status=pending")
        self.assertEqual(list_res.status_code, 200)
        items = list_res.json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["request_id"], request_id)

        # 5. Non-doctor (Nurse) attempting decision is forbidden (403)
        self.actor = self.nurse
        forbidden_dec = self.client.post(
            "/api/substitutions/decision",
            json={"request_id": request_id, "decision": "APPROVED"},
        )
        self.assertEqual(forbidden_dec.status_code, 403)

        # 6. Doctor reviews and approves the request
        self.actor = self.doctor
        decision_payload = {
            "request_id": request_id,
            "decision": "APPROVED",
            "doctor_notes": "Approved due to institutional inventory shortage.",
        }
        dec_res = self.client.post("/api/substitutions/decision", json=decision_payload)
        self.assertEqual(dec_res.status_code, 200, dec_res.text)
        dec_data = dec_res.json()
        self.assertEqual(dec_data["status"], "approved")
        self.assertTrue(dec_data["ready_for_dispensing"])
        self.assertIsNotNone(dec_data["audit_event_id"])

        # 7. Decided request cannot be decided again (409 Conflict)
        conflict_res = self.client.post("/api/substitutions/decision", json=decision_payload)
        self.assertEqual(conflict_res.status_code, 409)

        # 8. Query patient history via GET /api/substitutions/patient/{id}
        hist_res = self.client.get(f"/api/substitutions/patient/{self.patient.id}")
        self.assertEqual(hist_res.status_code, 200)
        history = hist_res.json()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "approved")
        self.assertEqual(history[0]["decided_by"], self.doctor.full_name)

        # 9. Query patient history via clinical identifier
        hist_ident_res = self.client.get(f"/api/substitutions/history/{self.patient.patient_identifier}")
        self.assertEqual(hist_ident_res.status_code, 200)
        self.assertEqual(len(hist_ident_res.json()), 1)

    def test_validation_errors_and_missing_resources_return_structured_json(self):
        """Invalid requests must return clean JSON errors with proper HTTP status codes."""
        self.actor = self.pharmacist

        # Invalid payload: whitespace only
        invalid_payload = {
            "patient_id": self.patient.id,
            "order_id": self.order.id,
            "medication_id": self.medication.id,
            "idempotency_key": "rest-idempotency-key-0002",
            "suggested_brand": "   ",
            "reason": "   ",
        }
        res_invalid = self.client.post("/api/substitutions/request", json=invalid_payload)
        self.assertEqual(res_invalid.status_code, 422)
        self.assertIn("detail", res_invalid.json())

        # Nonexistent patient
        not_found_payload = {
            "patient_id": 999999,
            "order_id": self.order.id,
            "medication_id": self.medication.id,
            "idempotency_key": "rest-idempotency-key-0003",
            "suggested_brand": "Valid Brand",
            "reason": "Valid reason.",
        }
        res_not_found = self.client.post("/api/substitutions/request", json=not_found_payload)
        self.assertEqual(res_not_found.status_code, 404)
        self.assertIn("detail", res_not_found.json())

        # Querying non-existent request ID
        res_missing_id = self.client.get("/api/substitutions/999999")
        self.assertEqual(res_missing_id.status_code, 404)

    def test_unauthenticated_request_is_rejected_with_401(self):
        """Requests without active credentials must receive 401 Unauthorized."""
        app.dependency_overrides.pop(get_current_user, None)
        res = self.client.post(
            "/api/substitutions/request",
            json={
                "patient_id": self.patient.id,
                "order_id": self.order.id,
                "medication_id": self.medication.id,
                "idempotency_key": "no-auth-idempotency-key",
                "suggested_brand": "Any Brand",
                "reason": "Any reason",
            },
        )
        self.assertEqual(res.status_code, 401)
