import sqlite3
import tempfile
import unittest
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.audit import GENESIS_PREV_HASH, AuditLedger
from agents.explicator import ExplicatorService
from agents.knowledge import DrugNormalizer, InteractionEvidence, KnowledgeService
from agents.resolution import SafetyResolutionEngine
from agents.risk.schemas import Finding
from agents.rules.pack_service import DEMO_RULE_PACK_PATH, RulePackService
from database.database import Base
from engine.event_service import MedicationEventService
from models.models import Medication, Patient


class TestAuditLedgerPipeline(unittest.TestCase):
    """
    Step 35 — Audit Ledger Pipeline Integration Tests:
    - Recording detection, resolution, explanation, and simulated-order events
    - Preservation of timestamp, actor, event_type, payload, prev_hash, hash
    - Append-only behavior (rejection of UPDATE / DELETE)
    - Hash-chain verification across full pipeline execution
    - Detection of SQLite record tampering (payload, hash, prev_hash)
    - Pure in-memory / temporary database isolation
    """

    def setUp(self):
        # Create an isolated temporary directory for test DBs
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = Path(self.temp_dir.name) / "test_audit_ledger.db"
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

        # Create isolated in-memory SQLite database for ORM tables
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionFactory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionFactory()

        # RulePackService with demo pack loaded into in-memory DB
        self.pack_service = RulePackService(session=self.db)
        self.pack_service.load_pack(DEMO_RULE_PACK_PATH)

        # KnowledgeService with interaction evidence
        self.normalizer = DrugNormalizer()
        self.knowledge_service = KnowledgeService(normalizer=self.normalizer)
        self.knowledge_service.add_interactions(
            [
                InteractionEvidence(
                    drug_a="warfarin",
                    drug_b="fluconazole",
                    description="Fluconazole significantly increases warfarin effect and bleeding risk.",
                    source="AEGIS_HACKATHON_DEMO",
                    source_version="1.0.0",
                    evidence_id="AEGIS-DEMO-EV-001",
                )
            ]
        )

        # Explicator
        self.explicator = ExplicatorService()

        # SafetyResolutionEngine wired with RulePackService, Explicator, and AuditLedger
        self.resolution_engine = SafetyResolutionEngine(
            rule_context=self.pack_service,
            explicator=self.explicator,
            audit_ledger=self.audit_ledger,
        )

        # MedicationEventService wired with AuditLedger and SafetyResolutionEngine
        self.event_service = MedicationEventService(
            knowledge_service=self.knowledge_service,
            resolution_engine=self.resolution_engine,
            audit_ledger=self.audit_ledger,
        )

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.temp_dir.cleanup()

    def test_full_pipeline_event_audit_logging(self):
        """
        Test that running the full pipeline records:
        1. Detection event (RISK_FINDING_DETECTED)
        2. Resolution event (SAFETY_RESOLUTION_EVALUATED)
        3. Explanation event (EXPLANATION_GENERATED)
        4. Simulated Order event (SIMULATED_ORDER_CREATED)
        """
        # Create test patient in SQLite
        patient = Patient(patient_identifier="MRN-AUDIT-001", name="Sarah Connor")
        self.db.add(patient)
        self.db.commit()
        self.db.refresh(patient)

        # Patient on Warfarin
        med1 = Medication(
            patient_id=patient.id,
            drug_name="Warfarin Sodium 5mg",
            dose="5",
            dose_unit="mg",
            status="active",
        )
        self.db.add(med1)
        self.db.commit()

        # Submit order for Fluconazole
        db_findings, pipeline_results = self.event_service.process_event_with_resolutions(
            db=self.db,
            patient_id=patient.id,
            event_type="MEDICATION_ORDERED",
            payload={"drug_name": "Fluconazole 100mg"},
            new_medication_name="Fluconazole 100mg",
        )

        self.assertEqual(len(db_findings), 1)
        self.assertEqual(len(pipeline_results), 1)
        self.assertEqual(pipeline_results[0].status, "actionable")
        self.assertIsNotNone(pipeline_results[0].simulated_order)
        self.assertIsNotNone(pipeline_results[0].explanation)

        # Retrieve all events recorded in the Audit Ledger
        records = self.audit_ledger.get_events()
        self.assertGreaterEqual(len(records), 4)

        event_types = [r.event_type for r in records]
        self.assertIn("RISK_FINDING_DETECTED", event_types)
        self.assertIn("SAFETY_RESOLUTION_EVALUATED", event_types)
        self.assertIn("EXPLANATION_GENERATED", event_types)
        self.assertIn("SIMULATED_ORDER_CREATED", event_types)

        # Verify preservation of provenance and cryptographic chaining
        self.assertEqual(records[0].prev_hash, GENESIS_PREV_HASH)
        for i in range(1, len(records)):
            self.assertEqual(records[i].prev_hash, records[i - 1].hash)
            self.assertIsNotNone(records[i].timestamp)
            self.assertIsNotNone(records[i].actor)
            self.assertIsInstance(records[i].payload, dict)

        # Verify chain integrity
        verification = self.audit_ledger.verify_chain()
        self.assertTrue(verification.is_valid)
        self.assertEqual(len(verification.errors), 0)
        self.assertIsNone(verification.tampered_record_id)

    def test_direct_resolution_pipeline_auditing(self):
        """Test SafetyResolutionEngine records resolution, explanation, and simulation events directly."""
        finding = Finding(
            rule_id="AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
            severity="review_required",
            title="Warfarin + Fluconazole Co-administration",
            description="High risk interaction.",
            action=None,
            inputs={"drug_a": "Warfarin", "drug_b": "Fluconazole", "patient_id": 999},
            trace={
                "rule_id": "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
                "evidence_id": "AEGIS-DEMO-EV-001",
                "source": "AEGIS_HACKATHON_DEMO",
            },
            source="AEGIS_HACKATHON_DEMO",
        )

        pipe_res = self.resolution_engine.resolve_finding_pipeline(finding)
        self.assertEqual(pipe_res.status, "actionable")

        records = self.audit_ledger.get_events()
        self.assertEqual(len(records), 3)

        self.assertEqual(records[0].event_type, "SAFETY_RESOLUTION_EVALUATED")
        self.assertEqual(records[0].actor, "safety_resolution_engine")
        self.assertEqual(records[0].payload["finding_id"], "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertTrue(records[0].payload["requires_cosign"])

        self.assertEqual(records[1].event_type, "EXPLANATION_GENERATED")
        self.assertEqual(records[1].actor, "explicator_service")
        self.assertEqual(records[1].payload["evidence_id"], "AEGIS-DEMO-EV-001")

        self.assertEqual(records[2].event_type, "SIMULATED_ORDER_CREATED")
        self.assertEqual(records[2].actor, "safety_resolution_engine")
        self.assertEqual(records[2].payload["status"], "pending_cosign")
        self.assertTrue(records[2].payload["requires_cosign"])
        self.assertFalse(records[2].payload["auto_execute"])

    def test_append_only_enforcement(self):
        """Test update_event and delete_event are strictly prohibited and raise RuntimeError."""
        with self.assertRaises(RuntimeError):
            self.audit_ledger.update_event(1, payload={"altered": True})

        with self.assertRaises(RuntimeError):
            self.audit_ledger.delete_event(1)

    def test_tamper_detection_in_sqlite(self):
        """Test verify_chain detects if any event payload, hash, or linkage is altered in SQLite."""
        # Append 3 valid events
        for i in range(3):
            self.audit_ledger.append_event(
                actor="test_actor",
                event_type=f"EVENT_{i}",
                payload={"index": i, "safe": True},
            )

        # Initial chain must be valid
        self.assertTrue(self.audit_ledger.verify_chain().is_valid)

        # Directly tamper with SQLite row 2 payload
        conn = sqlite3.connect(self.audit_db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE audit_ledger SET payload = ? WHERE id = 2", ('{"index": 2, "tampered": true}',))
        conn.commit()
        conn.close()

        # Chain verification MUST fail and identify tampered record ID 2
        verification = self.audit_ledger.verify_chain()
        self.assertFalse(verification.is_valid)
        self.assertEqual(verification.tampered_record_id, 2)
        self.assertGreaterEqual(len(verification.errors), 1)
        self.assertIn("hash mismatch", verification.errors[0].lower())


if __name__ == "__main__":
    unittest.main()
