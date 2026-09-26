import os
import tempfile
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.audit.ledger import AuditLedger
from app.dependencies import get_audit_ledger
from app.main import app
from database.database import Base, get_db
from engine.auth.dependencies import get_auth_service
from engine.auth.models import AuthBase
from engine.auth.providers.local import LocalDemoOTPProvider
from engine.auth.service import AuthService
from engine.drug_service import (
    get_medication_details,
    is_medication_recognized,
    normalize_medication_name,
    search_local_medications,
    validate_medication_input,
)
from models import Patient
from tests.test_client import LocalTestClient


class TestDrugValidationAndLookup(unittest.TestCase):
    """
    Test suite verifying local medication input validation, search autocomplete,
    and unknown medication rejection boundary.
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

        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = os.path.join(self.temp_dir.name, "test_drug_audit.db")
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

        self.local_provider = LocalDemoOTPProvider(demo_mode=True)
        self.auth_service = AuthService(
            audit_ledger=self.audit_ledger,
            sms_provider=self.local_provider,
            demo_mode=True,
        )
        self.auth_service.seed_demo_users(self.db)

        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_audit_ledger] = lambda: self.audit_ledger
        app.dependency_overrides[get_auth_service] = lambda: self.auth_service
        self.client = LocalTestClient(app)

        # Seed test patient with correct schema attributes
        self.patient = Patient(name="Test Patient", patient_identifier="PT-TEST-001", sex="Female")
        self.db.add(self.patient)
        self.db.commit()
        self.db.refresh(self.patient)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()
        app.dependency_overrides.clear()

    def test_medication_name_normalization(self):
        """Verify normalization handles salts, doses, casing, and spaces."""
        self.assertEqual(normalize_medication_name("  warfarin  "), "warfarin")
        self.assertEqual(normalize_medication_name("Warfarin Sodium"), "warfarin")
        self.assertEqual(normalize_medication_name("FLUCONAZOLE 100 MG"), "fluconazole")
        self.assertEqual(normalize_medication_name("Lisinopril Dihydrate"), "lisinopril")
        self.assertEqual(normalize_medication_name("Enoxaparin Sodium 80mg"), "enoxaparin")
        self.assertEqual(normalize_medication_name("Ciprofloxacin HCl 500 mg tab"), "ciprofloxacin")

    def test_known_drugs_recognized_locally(self):
        """Verify core clinical medications are recognized offline without internet."""
        for drug in ["Warfarin", "Fluconazole", "Enoxaparin", "Lisinopril", "Enalapril", "Aspirin", "Metoprolol", "Potassium Chloride"]:
            self.assertTrue(
                is_medication_recognized(self.db, drug),
                f"Drug '{drug}' should be recognized locally.",
            )
            # Case insensitive & salt variants
            self.assertTrue(is_medication_recognized(self.db, drug.lower()))
            self.assertTrue(is_medication_recognized(self.db, f"  {drug.upper()}  "))

    def test_unknown_arbitrary_drug_rejected(self):
        """Verify arbitrary strings like 'XYZUNKNOWN123' are rejected."""
        unknown_inputs = [
            "XYZUNKNOWN123",
            "RandomDrug999",
            "fake_med_12345",
            "invented_pill_abc",
            "NonExistentChemicalX",
        ]
        for bad_name in unknown_inputs:
            self.assertFalse(
                is_medication_recognized(self.db, bad_name),
                f"Unknown drug '{bad_name}' must be rejected.",
            )

    def test_validate_medication_input_structure(self):
        """Verify validate_medication_input returns structured validation result."""
        # Valid drug
        res_valid = validate_medication_input(self.db, "Fluconazole")
        self.assertTrue(res_valid["is_valid"])
        self.assertEqual(res_valid["status"], "RECOGNIZED")
        self.assertIsNotNone(res_valid["details"])
        self.assertEqual(res_valid["details"]["drug_name"], "Fluconazole")

        # Unknown drug
        res_invalid = validate_medication_input(self.db, "XYZUNKNOWN123")
        self.assertFalse(res_invalid["is_valid"])
        self.assertEqual(res_invalid["status"], "NOT_FOUND_IN_LOCAL_KNOWLEDGE_BASE")
        self.assertIn("not recognized", res_invalid["message"])
        self.assertTrue(len(res_invalid["possible_actions"]) >= 2)
        self.assertIsNone(res_invalid["details"])

    def test_api_drug_search_endpoint(self):
        """Test GET /drugs/search and /medications/search returns autocomplete list."""
        res = self.client.get("/drugs/search?q=war")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(any(d["drug_name"] == "Warfarin" for d in data))

        res_flu = self.client.get("/medications/search?q=flu")
        self.assertEqual(res_flu.status_code, 200)
        data_flu = res_flu.json()
        self.assertTrue(any(d["drug_name"] == "Fluconazole" for d in data_flu))

        # Unknown search returns empty list
        res_none = self.client.get("/drugs/search?q=xyzunknown123")
        self.assertEqual(res_none.status_code, 200)
        self.assertEqual(res_none.json(), [])

    def test_api_drug_validate_endpoint(self):
        """Test POST /drugs/validate returns recognition and helpful rejection guidance."""
        # Valid
        res_ok = self.client.post("/drugs/validate", json={"drug_name": "Warfarin"})
        self.assertEqual(res_ok.status_code, 200)
        self.assertTrue(res_ok.json()["is_valid"])

        # Unknown
        res_bad = self.client.post("/drugs/validate", json={"drug_name": "XYZUNKNOWN123"})
        self.assertEqual(res_bad.status_code, 200)
        data = res_bad.json()
        self.assertFalse(data["is_valid"])
        self.assertEqual(data["status"], "NOT_FOUND_IN_LOCAL_KNOWLEDGE_BASE")

    def test_patient_medication_creation_rejects_unknown_drug(self):
        """POST /patients/{id}/medications rejects unknown medications with HTTP 422."""
        res = self.client.post(
            f"/patients/{self.patient.id}/medications",
            json={
                "drug_name": "XYZUNKNOWN123",
                "dose": "500",
                "dose_unit": "mg",
                "status": "active",
            },
        )
        self.assertEqual(res.status_code, 422)
        self.assertIn("not recognized", res.json()["detail"])

    def test_patient_medication_creation_accepts_valid_drug(self):
        """POST /patients/{id}/medications accepts recognized local medications."""
        res = self.client.post(
            f"/patients/{self.patient.id}/medications",
            json={
                "drug_name": "Warfarin",
                "dose": "5",
                "dose_unit": "mg",
                "status": "active",
            },
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["drug_name"], "Warfarin")

    def test_events_endpoint_rejects_unknown_medication(self):
        """POST /events rejects unknown medications with HTTP 422."""
        res = self.client.post(
            "/events",
            json={
                "patient_id": self.patient.id,
                "event_type": "MEDICATION_PRESCRIBED",
                "payload": {"drug_name": "XYZUNKNOWN123", "dose": "100mg"},
                "new_medication_name": "XYZUNKNOWN123",
            },
        )
        self.assertEqual(res.status_code, 422)
        self.assertIn("not recognized", res.json()["detail"])


if __name__ == "__main__":
    unittest.main()
