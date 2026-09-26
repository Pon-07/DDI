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
from tests.test_client import LocalTestClient


class TestStep47DemoRuntime(unittest.TestCase):
    """
    Isolated integration tests for STEP 47:
    Local Demo Runtime Integration (Seeding, Scenarios, and Pipeline Verification).
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = os.path.join(self.temp_dir.name, "demo_test_audit.db")
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

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

    def test_demo_seed_and_status(self):
        """Verify seeding demo patients and checking demo status."""
        res_seed = self.client.post("/demo/seed")
        self.assertEqual(res_seed.status_code, 200)
        seed_data = res_seed.json()
        self.assertEqual(seed_data["status"], "seeded")
        self.assertIn("patient_1", seed_data["patients"])
        self.assertIn("patient_2", seed_data["patients"])
        self.assertIn("patient_3", seed_data["patients"])

        res_status = self.client.get("/demo/status")
        self.assertEqual(res_status.status_code, 200)
        status_data = res_status.json()
        self.assertEqual(status_data["status"], "ready")
        self.assertTrue(status_data["offline"])
        self.assertEqual(status_data["seeded_patients_count"], 3)
        self.assertEqual(len(status_data["scenarios"]), 3)

    def test_scenario_1_warfarin_fluconazole_inr(self):
        """Verify Scenario 1 triggered through API event pipeline."""
        res = self.client.post("/demo/run/1")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        pv = data["pipeline_verification"]
        self.assertTrue(pv["risk_detected"])
        self.assertEqual(pv["matched_rule_id"], "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertTrue(pv["rule_matched_correctly"])
        self.assertTrue(pv["simulated_order_cosign_enforced"])
        self.assertTrue(pv["audit_hash_chain_valid"])
        self.assertGreater(pv["total_audit_records"], 0)

        # Check provenance
        f0 = data["findings"][0]
        self.assertEqual(f0["source"], "AEGIS_HACKATHON_DEMO")
        self.assertEqual(f0["evidence_id"], "AEGIS-DEMO-EV-001")

        # Check simulated order
        so = data["simulated_orders"][0]
        self.assertEqual(so["status"], "pending_cosign")
        self.assertTrue(so["requires_cosign"])
        self.assertFalse(so["auto_execute"])
        self.assertTrue(so["is_simulated"])

    def test_scenario_2_enoxaparin_declining_renal(self):
        """Verify Scenario 2 triggered through API event pipeline."""
        res = self.client.post("/demo/run/2")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        pv = data["pipeline_verification"]
        self.assertTrue(pv["risk_detected"])
        self.assertEqual(pv["matched_rule_id"], "AEGIS-DEMO-002-ENOXAPARIN-RENAL")
        self.assertTrue(pv["rule_matched_correctly"])
        self.assertTrue(pv["simulated_order_cosign_enforced"])
        self.assertTrue(pv["audit_hash_chain_valid"])

        f0 = data["findings"][0]
        self.assertEqual(f0["source"], "AEGIS_HACKATHON_DEMO")
        self.assertEqual(f0["evidence_id"], "AEGIS-DEMO-EV-002")

    def test_scenario_3_duplicate_ace_inhibitor(self):
        """Verify Scenario 3 triggered through API event pipeline."""
        res = self.client.post("/demo/run/3")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        pv = data["pipeline_verification"]
        self.assertTrue(pv["risk_detected"])
        self.assertEqual(pv["matched_rule_id"], "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR")
        self.assertTrue(pv["rule_matched_correctly"])
        self.assertTrue(pv["simulated_order_cosign_enforced"])
        self.assertTrue(pv["audit_hash_chain_valid"])

        f0 = data["findings"][0]
        self.assertEqual(f0["source"], "AEGIS_HACKATHON_DEMO")
        self.assertEqual(f0["evidence_id"], "AEGIS-DEMO-EV-003")

    def test_invalid_scenario_id(self):
        """Verify invalid scenario returns 400 error."""
        res = self.client.post("/demo/run/99")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid scenario_id", res.json()["detail"])

    def test_demo_html_interface(self):
        """Verify offline HTML demo interface loads."""
        res = self.client.get("/demo")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("content-type", ""))
        self.assertIn("AEGIS Rx", res.text)
        self.assertIn("Official Hackathon Demo Scenarios", res.text)
        self.assertIn("Offline Verified", res.text)


if __name__ == "__main__":
    unittest.main()
