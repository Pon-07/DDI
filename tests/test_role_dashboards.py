import os
import tempfile
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.audit.ledger import AuditLedger
from app.main import app
from database.database import Base, get_db
from engine.auth.models import AuthBase
from engine.auth.providers.local import LocalDemoOTPProvider
from engine.auth.service import AuthService
from models import Lab, Medication, Patient
from tests.test_client import LocalTestClient


class TestRoleDashboards(unittest.TestCase):
    """
    Integration tests for Role-Specific Dashboards:
    - Doctor Dashboard (cosign queue, patient profiles, prescribing sandbox)
    - Nurse Dashboard (MAR records, vitals/lab entry)
    - Pharmacist Dashboard (DDI matrix, CYP enzymes, renal adjustments)
    - Admin Dashboard (system health, audit chain, gateway switcher)
    - Web Console HTML endpoint (/app)
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        AuthBase.metadata.create_all(bind=self.engine)

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        # Seed clinical patients
        p1 = Patient(patient_identifier="DEMO-PT-001", name="Sarah Jenkins", sex="F")
        p2 = Patient(patient_identifier="DEMO-PT-002", name="Robert Chen", sex="M")
        self.db.add_all([p1, p2])
        self.db.flush()

        m1 = Medication(patient_id=p1.id, drug_name="warfarin", dose="5", dose_unit="mg", frequency="daily")
        l1 = Lab(patient_id=p1.id, test_name="INR", value="3.4")
        self.db.add_all([m1, l1])
        self.db.commit()

        # Temp AuditLedger
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = os.path.join(self.temp_dir.name, "test_dash_audit.db")
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

        # Seed simulated order in audit ledger for cosign test
        self.audit_ledger.append_event(
            actor="SYSTEM_RESOLUTION_ENGINE",
            event_type="SIMULATED_ORDER_CREATED",
            payload={
                "simulation_id": "SIM-TEST-001",
                "patient_id": p1.id,
                "drug_name": "warfarin",
                "proposed_action": "Reduce warfarin dose by 50%",
                "action_type": "DOSE_REDUCTION",
                "rationale": "CYP2C9 inhibition from fluconazole",
                "requires_cosign": True,
            },
        )

        self.auth_service = AuthService(
            audit_ledger=self.audit_ledger,
            sms_provider=LocalDemoOTPProvider(demo_mode=True),
            demo_mode=True,
        )
        self.auth_service.seed_demo_users(self.db)

        app.dependency_overrides[get_db] = lambda: self.db
        from app.dependencies import get_audit_ledger
        from engine.auth.dependencies import get_auth_service
        app.dependency_overrides[get_audit_ledger] = lambda: self.audit_ledger
        app.dependency_overrides[get_auth_service] = lambda: self.auth_service

        self.client = LocalTestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        AuthBase.metadata.drop_all(bind=self.engine)
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_doctor_dashboard_data(self):
        """Verify Doctor dashboard returns patient rosters, pending cosigns, and metrics."""
        res = self.client.get("/api/dashboard/doctor")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["role"], "Doctor")
        self.assertIn("stats", data)
        self.assertGreaterEqual(data["stats"]["total_patients"], 2)
        self.assertEqual(data["stats"]["pending_cosigns_count"], 1)

        # Check pending cosign order
        self.assertEqual(len(data["pending_cosigns"]), 1)
        self.assertEqual(data["pending_cosigns"][0]["simulation_id"], "SIM-TEST-001")
        self.assertEqual(data["pending_cosigns"][0]["drug_name"], "warfarin")

    def test_nurse_dashboard_data(self):
        """Verify Nurse dashboard returns MAR records and assigned patient counts."""
        res = self.client.get("/api/dashboard/nurse")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["role"], "Nurse")
        self.assertIn("mar_schedule", data)
        self.assertGreaterEqual(len(data["mar_schedule"]), 1)
        self.assertEqual(data["mar_schedule"][0]["patient_name"], "Sarah Jenkins")

    def test_pharmacist_dashboard_data(self):
        """Verify Pharmacist dashboard returns pharmacology insights and DDI matrix."""
        res = self.client.get("/api/dashboard/pharmacist")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["role"], "Clinical Pharmacist")
        self.assertIn("pharmacology_insights", data)
        self.assertGreaterEqual(len(data["pharmacology_insights"]), 3)
        pairs = [p["pair"] for p in data["pharmacology_insights"]]
        self.assertIn("Warfarin + Fluconazole", pairs)

    def test_admin_dashboard_data(self):
        """Verify Admin dashboard returns audit chain integrity, users, and gateway status."""
        res = self.client.get("/api/dashboard/admin")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["role"], "Administrator")
        self.assertTrue(data["audit_chain"]["is_valid"])
        self.assertGreaterEqual(data["users_count"], 4)
        self.assertIn("sms_gateway", data)

    def test_clinical_console_html_rendering(self):
        """Verify /app renders complete HTML console with MICROMEDX branding and mobile auth flow."""
        res = self.client.get("/app")
        self.assertEqual(res.status_code, 200)
        html = res.text

        self.assertIn("MICROMEDX", html)
        self.assertIn("From Warning to Verified Action", html)
        self.assertIn("Clinical Authentication Portal", html)
        self.assertIn("Doctor", html)
        self.assertIn("Nurse", html)
        self.assertIn("Clinical Pharmacist", html)
        self.assertIn("Administrator", html)
        self.assertIn("otp-grid", html)
        self.assertIn("sms-debug-toast", html)

    def test_switch_sms_provider_endpoint(self):
        """Verify switching SMS gateway via API works smoothly."""
        res = self.client.post("/auth/providers/switch", json={"provider_name": "msg91"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["provider_status"]["provider"], "msg91")

        # Switch back to local
        res_back = self.client.post("/auth/providers/switch", json={"provider_name": "local"})
        self.assertEqual(res_back.status_code, 200)
        self.assertEqual(res_back.json()["provider_status"]["provider"], "local_demo")


if __name__ == "__main__":
    unittest.main()
