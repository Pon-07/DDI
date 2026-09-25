import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.audit.ledger import AuditLedger
from agents.explicator import ExplicatorService
from agents.knowledge import DrugNormalizer, KnowledgeService
from agents.resolution import (
    ResolutionCandidate,
    ResolutionPipelineResult,
    ResolutionResult,
    SafetyResolutionEngine,
    SimulatedOrder,
)
from agents.risk import Finding, RiskDetector
from agents.rules.schemas import RulePack, SafetyRule
from database.database import Base
from engine.event_service import MedicationEventService
from models.models import Event, Finding as DbFinding, Lab, Medication, Patient


class TestStep41OfflineFailureResilience(unittest.TestCase):
    """
    Isolated unit and integration tests for STEP 41 — Offline & Failure Resilience:
    1. Ollama unavailable
    2. Ollama/network error (no retry, immediate fallback)
    3. LLM explanation validation failure (reject wording, preserve finding/resolution)
    4. Missing/unknown knowledge (never infer, route to human review)
    5. Incomplete patient context (never guess, preserve data-needed)
    6. Inactive/deprecated rule (never participate in safety decisions)
    7. Audit failure (exposed explicitly, append-only protections)
    8. Network disabled (deterministic offline pipeline)
    9. Database/event failure (fail safely with rollback, no false findings/orders)
    """

    def setUp(self):
        # In-memory SQLite for complete isolation
        self.db_engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.db_engine)
        self.SessionFactory = sessionmaker(bind=self.db_engine, autoflush=False, autocommit=False)
        self.db = self.SessionFactory()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = Path(self.temp_dir.name) / "test_resilience_audit.db"

        self.normalizer = DrugNormalizer()
        self.knowledge_service = KnowledgeService(normalizer=self.normalizer)

        self.validated_rule = SafetyRule(
            rule_id="RULE-WARFARIN-FLUCONAZOLE",
            rule_type="drug_interaction",
            drug_a="warfarin",
            drug_b="fluconazole",
            source="DDInter",
            source_version="v2.1",
            evidence_id="EVID-WF-001",
            evidence_text="Fluconazole inhibits warfarin metabolism, significantly increasing INR and bleeding risk.",
            severity="Major",
            action="Hold fluconazole and monitor INR closely within 24 to 48 hours.",
            status="active",
        )
        self.rule_pack = RulePack(
            pack_name="ResilienceRulePack",
            version="1.0.0",
            rules=[self.validated_rule],
        )

        self.sample_finding = Finding(
            rule_id="RULE-WARFARIN-FLUCONAZOLE",
            severity="Major",
            title="Warfarin + Fluconazole Co-administration",
            description="Concomitant warfarin and fluconazole creates severe bleeding risk.",
            action=None,
            inputs={"drug_a": "warfarin", "drug_b": "fluconazole", "patient_id": 1},
            trace={
                "rule_id": "RULE-WARFARIN-FLUCONAZOLE",
                "source": "DDInter",
                "source_version": "v2.1",
                "evidence_id": "EVID-WF-001",
                "matched_pair": ["warfarin", "fluconazole"],
            },
            source="DDInter",
        )

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.db_engine)
        self.temp_dir.cleanup()

    def test_1_ollama_unavailable_fallback(self):
        """
        1. Ollama unavailable:
        - Deterministic explanation must still be generated.
        - Safety decision must continue normally.
        - Never treat Ollama failure as a clinical failure.
        """
        # Case A: Ollama client is None
        explicator_none = ExplicatorService(ollama_client=None)
        engine_none = SafetyResolutionEngine(
            rule_context=self.rule_pack,
            explicator=explicator_none,
        )
        res_none = engine_none.resolve_finding_pipeline(self.sample_finding)

        self.assertEqual(res_none.status, "actionable")
        self.assertIsNotNone(res_none.simulated_order)
        self.assertEqual(res_none.simulated_order.proposed_action, "Hold fluconazole and monitor INR closely within 24 to 48 hours.")
        self.assertIsNotNone(res_none.explanation)
        self.assertFalse(res_none.explanation.is_llm_enhanced)
        self.assertIn("DDInter", res_none.explanation.full_text)
        self.assertIn("EVID-WF-001", res_none.explanation.full_text)

        # Case B: Ollama client raises ConnectionRefusedError
        mock_failing_ollama = MagicMock(side_effect=ConnectionRefusedError("Connection refused on port 11434"))
        explicator_refused = ExplicatorService(ollama_client=mock_failing_ollama)
        engine_refused = SafetyResolutionEngine(
            rule_context=self.rule_pack,
            explicator=explicator_refused,
        )
        res_refused = engine_refused.resolve_finding_pipeline(self.sample_finding)

        self.assertEqual(res_refused.status, "actionable")
        self.assertIsNotNone(res_refused.simulated_order)
        self.assertIsNotNone(res_refused.explanation)
        self.assertFalse(res_refused.explanation.is_llm_enhanced)
        self.assertFalse(res_refused.explanation.is_fallback)

    def test_2_ollama_network_error_immediate_fallback_no_retry(self):
        """
        2. Ollama/network error:
        - No external network retry.
        - Fall back immediately to deterministic explanation.
        """
        call_count = 0

        def failing_network_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise OSError("Network unreachable")

        mock_client = MagicMock(side_effect=failing_network_call)
        explicator = ExplicatorService(ollama_client=mock_client)

        report = explicator.explicate(self.sample_finding)

        # Exactly 1 call was attempted (no network retries)
        self.assertEqual(call_count, 1)
        # Deterministic explanation was delivered immediately
        self.assertIsNotNone(report)
        self.assertFalse(report.is_llm_enhanced)
        self.assertIn("RULE-WARFARIN-FLUCONAZOLE", report.full_text)
        self.assertIn("warfarin", report.medications)
        self.assertIn("fluconazole", report.medications)

    def test_3_llm_explanation_validation_failure_rejected(self):
        """
        3. LLM explanation validation failure:
        - Reject the generated wording.
        - Use deterministic template.
        - Never modify the underlying finding or resolution.
        """
        # Mock Ollama returning hallucinated foreign drug ("digoxin") and altered numbers
        hallucinated_text = (
            "Finding ID: RULE-WARFARIN-FLUCONAZOLE Source: DDInter Evidence ID: EVID-WF-001\n"
            "Patient should immediately take 500mg of digoxin and 99.9mg of aspirin."
        )
        mock_hallucinating_client = MagicMock(return_value={"response": hallucinated_text})
        explicator = ExplicatorService(ollama_client=mock_hallucinating_client)

        engine = SafetyResolutionEngine(
            rule_context=self.rule_pack,
            explicator=explicator,
        )

        orig_finding_data = self.sample_finding.model_dump()
        pipe_res = engine.resolve_finding_pipeline(self.sample_finding)

        # 1. Hallucinated wording was rejected
        self.assertFalse(pipe_res.explanation.is_llm_enhanced)
        self.assertNotIn("digoxin", pipe_res.explanation.full_text.lower())
        self.assertNotIn("99.9", pipe_res.explanation.full_text)

        # 2. Deterministic template was used
        self.assertIn("Hold fluconazole and monitor INR closely", pipe_res.explanation.full_text)

        # 3. Finding and resolution state remained completely unmodified
        self.assertEqual(self.sample_finding.model_dump(), orig_finding_data)
        self.assertEqual(pipe_res.status, "actionable")
        self.assertEqual(pipe_res.simulated_order.drug_name, "warfarin")

    def test_4_missing_unknown_knowledge_routes_to_human_review(self):
        """
        4. Missing/unknown knowledge:
        - Never infer an interaction or treatment.
        - Route appropriately to human review/data-needed state.
        """
        # Unknown drug pair not in knowledge base or rules
        unknown_finding = Finding(
            rule_id="RULE-UNKNOWN-NEW-DRUG",
            severity="undetermined",
            title="Unknown Drug Safety Evaluation",
            description="No published interaction evidence exists for this drug pair.",
            action=None,
            inputs={"drug_a": "ExperimentalDrugX", "drug_b": "UnknownCompoundY"},
            source="Unknown",
        )

        engine = SafetyResolutionEngine(rule_context=self.rule_pack)
        res = engine.resolve_finding_pipeline(unknown_finding)

        # Must not infer or fabricate any action
        self.assertEqual(res.status, "requires_human_review")
        self.assertEqual(len(res.ranked_candidates), 0)
        self.assertIsNone(res.simulated_order)
        self.assertIn("No explicit validated clinical action is present", res.review_reason)

    def test_5_incomplete_patient_context_preserves_missing_data_state(self):
        """
        5. Incomplete patient context:
        - Never guess missing values.
        - Preserve explicit missing-data state.
        """
        finding_missing_data = Finding(
            rule_id="RULE-RENAL-CHECK",
            severity="Major",
            title="Renal clearance check needed",
            description="Dose adjustment requires current renal clearance.",
            action=None,
            inputs={"drug_a": "Metformin", "data_needed": ["serum_creatinine", "eGFR"]},
            source="openFDA",
        )

        engine = SafetyResolutionEngine(rule_context=self.rule_pack)
        res = engine.resolve_finding_pipeline(finding_missing_data)

        self.assertEqual(res.status, "requires_human_review")
        self.assertIsNone(res.simulated_order)
        self.assertEqual(len(res.safety_filtered_candidates), 1)
        self.assertEqual(res.safety_filtered_candidates[0].action_type, "missing_data_review")
        self.assertIn("serum_creatinine, eGFR", res.review_reason)

    def test_6_inactive_deprecated_rule_never_participates(self):
        """
        6. Inactive/deprecated rule:
        - Must never participate in active safety decisions.
        """
        inactive_rule = SafetyRule(
            rule_id="RULE-INACTIVE-TEST",
            rule_type="drug_interaction",
            drug_a="drug_alpha",
            drug_b="drug_beta",
            source="DDInter",
            source_version="v1.0",
            evidence_id="EVID-INACTIVE",
            evidence_text="Old interaction.",
            severity="Major",
            action="Do not use drug_alpha with drug_beta.",
            status="inactive",  # INACTIVE
        )
        deprecated_rule = SafetyRule(
            rule_id="RULE-DEP-TEST",
            rule_type="drug_interaction",
            drug_a="drug_alpha",
            drug_b="drug_beta",
            source="DDInter",
            source_version="v1.0",
            evidence_id="EVID-DEP",
            evidence_text="Deprecated interaction.",
            severity="Major",
            action="Deprecated action.",
            status="deprecated",  # DEPRECATED
        )
        pack = RulePack(pack_name="InactivePack", version="1.0", rules=[inactive_rule, deprecated_rule])
        engine = SafetyResolutionEngine(rule_context=pack)

        finding = Finding(
            rule_id="RULE-INACTIVE-TEST",
            severity="Major",
            title="Test Inactive",
            description="Inactive rule finding",
            action=None,
            inputs={"drug_a": "drug_alpha", "drug_b": "drug_beta"},
            source="DDInter",
        )
        res = engine.resolve_finding_pipeline(finding)

        self.assertEqual(res.status, "requires_human_review")
        self.assertEqual(len(res.ranked_candidates), 0)
        self.assertIsNone(res.simulated_order)

    def test_7_audit_failure_exposed_explicitly(self):
        """
        7. Audit failure:
        - Do not silently claim that an event was successfully audited.
        - Preserve safety integrity and expose the audit failure explicitly.
        - Never bypass append-only/hash-chain protections.
        """
        # A. If audit ledger append fails, the error must be exposed explicitly
        mock_failing_ledger = MagicMock()
        mock_failing_ledger.append_event.side_effect = sqlite3.OperationalError("database is locked")

        engine = SafetyResolutionEngine(
            rule_context=self.rule_pack,
            audit_ledger=mock_failing_ledger,
        )

        with self.assertRaises(sqlite3.OperationalError):
            engine.resolve_finding_pipeline(self.sample_finding)

        # B. Append-only enforcement on AuditLedger
        ledger = AuditLedger(db_path=self.audit_db_path)
        rec = ledger.append_event(actor="tester", event_type="TEST_EVENT", payload={"key": "val"})
        self.assertIsNotNone(rec.hash)

        # Attempt to modify or delete must raise RuntimeError
        with self.assertRaises(RuntimeError):
            ledger.update_event(rec.id, actor="hacker")

        with self.assertRaises(RuntimeError):
            ledger.delete_event(rec.id)

        # C. Hash chain tampering detection
        verify_ok = ledger.verify_chain()
        self.assertTrue(verify_ok.is_valid)

        # Directly tamper with SQLite table to simulate corruption/tampering
        tampered_hash = "0" * 64
        with ledger._connection() as conn:
            conn.execute("UPDATE audit_ledger SET hash = ? WHERE id = ?", (tampered_hash, rec.id))
            conn.commit()

        verify_tampered = ledger.verify_chain()
        self.assertFalse(verify_tampered.is_valid)
        self.assertIn("hash mismatch", verify_tampered.errors[0].lower())
        self.assertEqual(verify_tampered.tampered_record_id, rec.id)

    def test_8_network_disabled_deterministic_pipeline(self):
        """
        8. Network disabled:
        - Entire deterministic pipeline must continue without external services.
        - No hidden HTTP/API calls.
        """
        orig_socket = socket.socket

        def blocked_socket(*args, **kwargs):
            raise RuntimeError("Network access strictly forbidden in offline test")

        with patch("socket.socket", side_effect=blocked_socket):
            # Run complete pipeline offline
            offline_db_path = Path(self.temp_dir.name) / "offline_audit.db"
            ledger = AuditLedger(db_path=offline_db_path)
            explicator = ExplicatorService(ollama_client=None)
            engine = SafetyResolutionEngine(
                rule_context=self.rule_pack,
                explicator=explicator,
                audit_ledger=ledger,
            )

            res = engine.resolve_finding_pipeline(
                finding=self.sample_finding,
                existing_medication={
                    "id": 10,
                    "patient_id": 1,
                    "drug_name": "Warfarin Sodium",
                    "dose": "5",
                    "dose_unit": "mg",
                    "route": "oral",
                    "frequency": "daily",
                },
            )

            self.assertEqual(res.status, "actionable")
            self.assertIsNotNone(res.simulated_order)
            self.assertEqual(res.simulated_order.dose, "5")
            self.assertEqual(res.simulated_order.dose_unit, "mg")
            self.assertIsNotNone(res.explanation)
            self.assertFalse(res.explanation.is_llm_enhanced)

            # Audit ledger has recorded events without network
            events = ledger.get_events()
            self.assertGreaterEqual(len(events), 2)
            self.assertTrue(ledger.verify_chain().is_valid)

    def test_9_database_event_failure_safe_rollback(self):
        """
        9. Database/event failure:
        - Fail safely without creating false findings or simulated orders.
        """
        event_service = MedicationEventService(
            knowledge_service=self.knowledge_service,
            rule_context=self.rule_pack,
        )

        patient = Patient(id=999, name="Test Patient", patient_identifier="PAT-999")
        self.db.add(patient)
        self.db.commit()

        # Simulate database failure during process_event by mocking commit to raise IntegrityError
        with patch.object(self.db, "commit", side_effect=sqlite3.IntegrityError("Simulated DB integrity failure")):
            with self.assertRaises(sqlite3.IntegrityError):
                event_service.process_event(
                    db=self.db,
                    patient_id=999,
                    event_type="MEDICATION_ORDERED",
                    payload={"drug_name": "Warfarin"},
                )

        # After rollback, no false events or findings were persisted
        persisted_events = self.db.query(Event).filter(Event.patient_id == 999).all()
        self.assertEqual(len(persisted_events), 0)

        persisted_findings = self.db.query(DbFinding).filter(DbFinding.patient_id == 999).all()
        self.assertEqual(len(persisted_findings), 0)


if __name__ == "__main__":
    unittest.main()
