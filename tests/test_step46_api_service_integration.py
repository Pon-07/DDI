import os
import tempfile
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.audit.ledger import AuditLedger
from agents.explicator.service import ExplicatorService
from agents.knowledge import DrugNormalizer, KnowledgeService
from agents.resolution.resolver import SafetyResolutionEngine
from app.dependencies import (
    get_audit_ledger,
    get_event_service,
    get_explicator_service,
    get_resolution_engine,
)
from app.main import app
from database.database import Base, get_db
from engine.event_service import MedicationEventService
from models import Lab, Medication, Patient
from tests.test_client import LocalTestClient


class TestStep46ApiServiceIntegration(unittest.TestCase):
    """
    Isolated integration tests for STEP 46:
    Exposing the AEGIS Rx medication safety pipeline through FastAPI.
    Covers all 8 endpoint capability areas:
    1. Health / status
    2. Patient / context retrieval
    3. Event submission
    4. Risk findings retrieval
    5. Finding -> Resolution result
    6. Explanation retrieval
    7. Simulated-order / cosign status
    8. Audit-chain verification
    """

    def setUp(self):
        # 1. In-memory SQLite DB with StaticPool to share connection
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        # 2. Temp AuditLedger
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = os.path.join(self.temp_dir.name, "test_api_audit.db")
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

        # 3. Services
        self.normalizer = DrugNormalizer()
        self.knowledge_service = KnowledgeService(normalizer=self.normalizer)
        self.explicator = ExplicatorService()
        self.resolution_engine = SafetyResolutionEngine(
            explicator=self.explicator,
            audit_ledger=self.audit_ledger,
        )
        self.event_service = MedicationEventService(
            knowledge_service=self.knowledge_service,
            resolution_engine=self.resolution_engine,
            rule_context=self.resolution_engine,
            explicator=self.explicator,
            audit_ledger=self.audit_ledger,
        )

        # 4. Dependency overrides
        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_audit_ledger] = lambda: self.audit_ledger
        app.dependency_overrides[get_explicator_service] = lambda: self.explicator
        app.dependency_overrides[get_resolution_engine] = lambda: self.resolution_engine
        app.dependency_overrides[get_event_service] = lambda: self.event_service

        self.client = LocalTestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.temp_dir.cleanup()

    def test_01_health_and_status_endpoints(self):
        """1. Health & status endpoints return structured offline system info."""
        res_health = self.client.get("/health")
        self.assertEqual(res_health.status_code, 200)
        self.assertEqual(res_health.json(), {"status": "ok"})

        res_db = self.client.get("/health/db")
        self.assertEqual(res_db.status_code, 200)
        self.assertEqual(res_db.json().get("database"), "connected")

        res_status = self.client.get("/status")
        self.assertEqual(res_status.status_code, 200)
        status_data = res_status.json()
        self.assertEqual(status_data["status"], "ok")
        self.assertTrue(status_data["offline_capable"])
        self.assertEqual(status_data["database"], "connected")
        self.assertGreater(status_data["active_rules_count"], 0)
        self.assertTrue(status_data["audit_chain_valid"])

    def test_02_patient_and_context_retrieval(self):
        """2. Patient creation and complete clinical context retrieval."""
        # Create patient
        p_res = self.client.post("/patients", json={
            "patient_identifier": "TEST-PT-001",
            "name": "Jane Doe",
            "sex": "F",
        })
        self.assertEqual(p_res.status_code, 201)
        p_id = p_res.json()["id"]

        # Add medication & lab
        self.client.post(f"/patients/{p_id}/medications", json={
            "drug_name": "Warfarin",
            "dose": "5",
            "dose_unit": "mg",
            "status": "active",
        })
        self.client.post(f"/patients/{p_id}/labs", json={
            "test_name": "INR",
            "value": "2.8",
        })

        # Retrieve context
        ctx_res = self.client.get(f"/patients/{p_id}/context")
        self.assertEqual(ctx_res.status_code, 200)
        ctx = ctx_res.json()
        self.assertEqual(ctx["patient"]["name"], "Jane Doe")
        self.assertEqual(len(ctx["medications"]), 1)
        self.assertEqual(ctx["medications"][0]["drug_name"], "Warfarin")
        self.assertEqual(len(ctx["labs"]), 1)
        self.assertEqual(ctx["labs"][0]["test_name"], "INR")
        self.assertIsInstance(ctx["findings"], list)

    def test_03_event_submission_pipeline(self):
        """3. Clinical event submission through existing MedicationEventService."""
        # Patient on warfarin + INR
        p = Patient(patient_identifier="TEST-PT-002", name="John Smith")
        self.db.add(p)
        self.db.commit()
        self.db.refresh(p)
        self.db.add(Medication(patient_id=p.id, drug_name="warfarin", dose="5", dose_unit="mg", status="active"))
        self.db.add(Lab(patient_id=p.id, test_name="INR", value="2.2"))
        self.db.add(Lab(patient_id=p.id, test_name="INR", value="3.5"))
        self.db.commit()

        # Submit event: Prescribing fluconazole
        evt_res = self.client.post("/events", json={
            "patient_id": p.id,
            "event_type": "MEDICATION_PRESCRIBED",
            "payload": {"drug_name": "fluconazole", "dose": "200mg"},
            "new_medication_name": "fluconazole",
        })
        self.assertEqual(evt_res.status_code, 201)
        data = evt_res.json()
        self.assertEqual(data["patient_id"], p.id)
        self.assertEqual(data["event_type"], "MEDICATION_PRESCRIBED")
        self.assertGreater(len(data["affected_findings"]), 0)
        self.assertEqual(data["affected_findings"][0]["rule_id"], "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertGreater(len(data["resolutions"]), 0)
        self.assertEqual(data["resolutions"][0]["status"], "actionable")
        self.assertTrue(data["audit_logged"])

    def test_04_risk_findings_retrieval(self):
        """4. Retrieval of risk findings by finding_id and by patient_id."""
        p = Patient(patient_identifier="TEST-PT-003", name="Alice Wonderland")
        self.db.add(p)
        self.db.commit()
        self.db.refresh(p)
        self.db.add(Medication(patient_id=p.id, drug_name="lisinopril", dose="20", dose_unit="mg", status="active"))
        self.db.commit()

        # Trigger duplicate therapy finding
        evt_res = self.client.post("/events", json={
            "patient_id": p.id,
            "event_type": "MEDICATION_ORDERED",
            "payload": {"drug_name": "enalapril"},
            "new_medication_name": "enalapril",
        })
        finding_id = evt_res.json()["affected_findings"][0]["id"]

        # Retrieve specific finding
        f_res = self.client.get(f"/findings/{finding_id}")
        self.assertEqual(f_res.status_code, 200)
        f_data = f_res.json()
        self.assertEqual(f_data["id"], finding_id)
        self.assertEqual(f_data["rule_id"], "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR")
        self.assertEqual(f_data["trace"]["source"], "AEGIS_HACKATHON_DEMO")
        self.assertEqual(f_data["trace"]["evidence_id"], "AEGIS-DEMO-EV-003")

        # Retrieve patient findings
        pf_res = self.client.get(f"/patients/{p.id}/findings")
        self.assertEqual(pf_res.status_code, 200)
        self.assertEqual(len(pf_res.json()), 1)

    def test_05_finding_to_resolution_result(self):
        """5. Deterministic resolution retrieval for a finding."""
        p = Patient(patient_identifier="TEST-PT-004", name="Bob Builder")
        self.db.add(p)
        self.db.commit()
        self.db.refresh(p)
        self.db.add(Medication(patient_id=p.id, drug_name="enoxaparin", dose="40", dose_unit="mg", status="active"))
        self.db.add(Lab(patient_id=p.id, test_name="Creatinine", value="1.0"))
        self.db.commit()

        evt_res = self.client.post("/events", json={
            "patient_id": p.id,
            "event_type": "LAB_RESULT_RECORDED",
            "payload": {"test_name": "Creatinine", "value": "2.4"},
        })
        finding_id = evt_res.json()["affected_findings"][0]["id"]

        res_res = self.client.get(f"/findings/{finding_id}/resolution")
        self.assertEqual(res_res.status_code, 200)
        r_data = res_res.json()
        self.assertEqual(r_data["status"], "actionable")
        self.assertGreater(len(r_data["ranked_candidates"]), 0)
        self.assertEqual(r_data["rule_id"], "AEGIS-DEMO-002-ENOXAPARIN-RENAL")
        self.assertEqual(r_data["evidence_id"], "AEGIS-DEMO-EV-002")
        self.assertTrue(r_data["requires_cosign"])

    def test_06_explanation_retrieval(self):
        """6. Explanation report retrieval with provenance and deterministic fallback."""
        p = Patient(patient_identifier="TEST-PT-005", name="Charlie Brown")
        self.db.add(p)
        self.db.commit()
        self.db.refresh(p)
        self.db.add(Medication(patient_id=p.id, drug_name="warfarin", dose="5", dose_unit="mg", status="active"))
        self.db.add(Lab(patient_id=p.id, test_name="INR", value="3.8"))
        self.db.commit()

        evt_res = self.client.post("/events", json={
            "patient_id": p.id,
            "event_type": "MEDICATION_PRESCRIBED",
            "payload": {"drug_name": "fluconazole"},
            "new_medication_name": "fluconazole",
        })
        finding_id = evt_res.json()["affected_findings"][0]["id"]

        exp_res = self.client.get(f"/findings/{finding_id}/explanation")
        self.assertEqual(exp_res.status_code, 200)
        exp = exp_res.json()
        self.assertEqual(exp["rule_id"], "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertIn("warfarin", exp["full_text"].lower())
        self.assertIn("fluconazole", exp["full_text"].lower())
        self.assertTrue(exp["is_deterministic_fallback"])

    def test_07_simulated_order_and_cosign_enforcement(self):
        """7. Simulated order remains pending-cosign, never auto-executes, and records clinician cosign."""
        p = Patient(patient_identifier="TEST-PT-006", name="David Copperfield")
        self.db.add(p)
        self.db.commit()
        self.db.refresh(p)
        self.db.add(Medication(patient_id=p.id, drug_name="lisinopril", dose="20", dose_unit="mg", status="active"))
        self.db.commit()

        evt_res = self.client.post("/events", json={
            "patient_id": p.id,
            "event_type": "MEDICATION_PRESCRIBED",
            "payload": {"drug_name": "enalapril"},
            "new_medication_name": "enalapril",
        })
        finding_id = evt_res.json()["affected_findings"][0]["id"]

        # Fetch simulated order
        so_res = self.client.get(f"/findings/{finding_id}/simulated-order")
        self.assertEqual(so_res.status_code, 200)
        so = so_res.json()
        self.assertEqual(so["status"], "pending_cosign")
        self.assertTrue(so["requires_cosign"])
        self.assertFalse(so["auto_execute"])
        self.assertTrue(so["is_simulated"])
        sim_id = so["simulation_id"]

        # List simulated orders
        list_res = self.client.get("/simulated-orders")
        self.assertEqual(list_res.status_code, 200)
        self.assertGreater(len(list_res.json()), 0)

        # Get specific simulated order
        get_res = self.client.get(f"/simulated-orders/{sim_id}")
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json()["simulation_id"], sim_id)

        # Cosign order as clinician
        cosign_res = self.client.post(f"/simulated-orders/{sim_id}/cosign", json={
            "cosigned_by": "Dr. Sarah Connor, MD",
            "decision": "approved",
            "clinical_notes": "Holding redundant ACE-inhibitor.",
        })
        self.assertEqual(cosign_res.status_code, 200)
        cosign_data = cosign_res.json()
        self.assertEqual(cosign_data["status"], "cosigned_approved")
        self.assertFalse(cosign_data["auto_execute"])
        self.assertIn("Prescriptions are NOT automatically executed", cosign_data["message"])

        # Check updated status
        status_check = self.client.get(f"/simulated-orders/{sim_id}")
        self.assertEqual(status_check.status_code, 200)
        self.assertEqual(status_check.json()["status"], "cosigned_approved")

    def test_08_audit_chain_verification_and_event_retrieval(self):
        """8. Cryptographic hash-chain verification and audit retrieval."""
        # Query audit verify
        v_res = self.client.get("/audit/verify")
        self.assertEqual(v_res.status_code, 200)
        v_data = v_res.json()
        self.assertTrue(v_data["is_valid"])
        self.assertGreaterEqual(v_data["total_records"], 0)

        # Ingest an event to generate records
        p = Patient(patient_identifier="TEST-PT-007", name="Eva Green")
        self.db.add(p)
        self.db.commit()
        self.db.refresh(p)
        self.client.post("/events", json={
            "patient_id": p.id,
            "event_type": "LAB_RESULT_RECORDED",
            "payload": {"test_name": "Creatinine", "value": "1.1"},
        })

        # Check audit events
        events_res = self.client.get("/audit/events")
        self.assertEqual(events_res.status_code, 200)
        events = events_res.json()
        self.assertGreater(len(events), 0)

        # Verify chain remains cryptographically valid after events
        v_res2 = self.client.get("/audit/verify")
        self.assertEqual(v_res2.status_code, 200)
        self.assertTrue(v_res2.json()["is_valid"])
        self.assertGreater(v_res2.json()["total_records"], 0)


if __name__ == "__main__":
    unittest.main()
