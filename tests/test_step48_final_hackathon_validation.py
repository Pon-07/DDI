import copy
import hashlib
import os
from pathlib import Path
import socket
import tempfile
import unittest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from agents.audit.ledger import AuditLedger
from agents.audit.verifier import verify_audit_chain
from agents.explicator.service import ExplicatorService
from agents.knowledge import DrugNormalizer, KnowledgeService
from agents.resolution.resolver import SafetyResolutionEngine
from agents.resolution.schemas import ResolutionCandidate, SimulatedOrder
from agents.rules.schemas import RulePack, SafetyRule
from database.database import Base
from engine.event_service import MedicationEventService
from models import Finding, Lab, Medication, Patient


class TestStep48FinalHackathonValidation(unittest.TestCase):
    """
    Comprehensive hackathon validation suite covering all AEGIS Rx requirements:
    Category B: Three official demo cases
    Category C: Six negative and resilience cases
    Category D: Safety guarantees (no invented values, no auto-exec, cosign mandatory, deterministic)
    Category E: Offline guarantees (zero external network, Ollama optional, deterministic fallback)
    Category F: Integrity (audit hash chain, tamper detection, provenance, zero prod db touch)
    Category G: Determinism (identical repeated executions yield equivalent results)
    """

    def setUp(self):
        # Isolated in-memory DB
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.session = self.SessionLocal()

        # Isolated temporary audit ledger
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = os.path.join(self.temp_dir.name, "step48_audit.db")
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

        self.normalizer = DrugNormalizer()
        self.knowledge_service = KnowledgeService(normalizer=self.normalizer)
        self.explicator = ExplicatorService(ollama_client=None)  # Explicitly offline, no Ollama
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

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(bind=self.engine)
        self.temp_dir.cleanup()

    # =========================================================================
    # CATEGORY B: THREE OFFICIAL DEMO CASES
    # =========================================================================

    def test_b1_case1_warfarin_fluconazole_inr(self):
        """B.1: Warfarin + fluconazole + rising INR: detection, provenance, resolution, explanation, simulated order, audit."""
        p = Patient(patient_identifier="VAL-DEMO-001", name="Sarah Jenkins", sex="F")
        self.session.add(p)
        self.session.commit()
        self.session.add(Medication(patient_id=p.id, drug_name="warfarin", dose="5", dose_unit="mg", status="active"))
        self.session.add(Lab(patient_id=p.id, test_name="INR", value="2.1"))
        self.session.add(Lab(patient_id=p.id, test_name="INR", value="3.4"))
        self.session.commit()

        findings, resolutions = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="MEDICATION_PRESCRIBED",
            payload={"drug_name": "fluconazole", "dose": "200mg"},
            new_medication_name="fluconazole",
        )
        self.assertGreater(len(findings), 0)
        f = findings[0]
        self.assertEqual(f.rule_id, "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertEqual(f.trace.get("source"), "AEGIS_HACKATHON_DEMO")
        self.assertEqual(f.trace.get("evidence_id"), "AEGIS-DEMO-EV-001")

        self.assertGreater(len(resolutions), 0)
        res = resolutions[0]
        self.assertEqual(res.status, "actionable")
        self.assertTrue(res.requires_cosign)
        self.assertIsNotNone(res.simulated_order)
        self.assertEqual(res.simulated_order.status, "pending_cosign")
        self.assertFalse(res.simulated_order.auto_execute)

        self.assertIsNotNone(res.explanation)
        self.assertIn("AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE", res.explanation.full_text)

        verification = self.audit_ledger.verify_chain()
        self.assertTrue(verification.is_valid)
        self.assertGreater(verification.total_records, 0)

    def test_b2_case2_enoxaparin_declining_renal(self):
        """B.2: Enoxaparin + declining renal context: detection, provenance, resolution, simulated order, audit."""
        p = Patient(patient_identifier="VAL-DEMO-002", name="Robert Chen", sex="M")
        self.session.add(p)
        self.session.commit()
        self.session.add(Medication(patient_id=p.id, drug_name="enoxaparin", dose="40", dose_unit="mg", status="active"))
        self.session.add(Lab(patient_id=p.id, test_name="Creatinine", value="1.0"))
        self.session.commit()

        findings, resolutions = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="LAB_RESULT_RECORDED",
            payload={"test_name": "Creatinine", "value": "2.4", "unit": "mg/dL"},
        )
        self.assertGreater(len(findings), 0)
        f = findings[0]
        self.assertEqual(f.rule_id, "AEGIS-DEMO-002-ENOXAPARIN-RENAL")
        self.assertEqual(f.trace.get("evidence_id"), "AEGIS-DEMO-EV-002")

        res = resolutions[0]
        self.assertEqual(res.status, "actionable")
        self.assertEqual(res.simulated_order.drug_name, "enoxaparin")
        self.assertEqual(res.simulated_order.status, "pending_cosign")
        self.assertFalse(res.simulated_order.auto_execute)
        self.assertTrue(self.audit_ledger.verify_chain().is_valid)

    def test_b3_case3_duplicate_ace_inhibitor(self):
        """B.3: Duplicate ACE-inhibitor therapy: detection, provenance, resolution, simulated order, audit."""
        p = Patient(patient_identifier="VAL-DEMO-003", name="Elena Rostova", sex="F")
        self.session.add(p)
        self.session.commit()
        self.session.add(Medication(patient_id=p.id, drug_name="lisinopril", dose="20", dose_unit="mg", status="active"))
        self.session.commit()

        findings, resolutions = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="MEDICATION_PRESCRIBED",
            payload={"drug_name": "enalapril", "dose": "10mg"},
            new_medication_name="enalapril",
        )
        self.assertGreater(len(findings), 0)
        f = findings[0]
        self.assertEqual(f.rule_id, "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR")
        self.assertEqual(f.trace.get("evidence_id"), "AEGIS-DEMO-EV-003")

        res = resolutions[0]
        self.assertEqual(res.status, "actionable")
        self.assertEqual(res.simulated_order.status, "pending_cosign")
        self.assertFalse(res.simulated_order.auto_execute)
        self.assertTrue(self.audit_ledger.verify_chain().is_valid)

    # =========================================================================
    # CATEGORY C: NEGATIVE AND RESILIENCE CASES
    # =========================================================================

    def test_c1_missing_required_context(self):
        """C.1: Missing required context -> explicitly marks missing data, never fabricates numbers."""
        p = Patient(patient_identifier="VAL-NEG-001", name="Missing Lab Patient", sex="M")
        self.session.add(p)
        self.session.commit()
        self.session.add(Medication(patient_id=p.id, drug_name="enoxaparin", dose="40", dose_unit="mg", status="active"))
        self.session.commit()

        findings, resolutions = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="MEDICATION_REVIEWED",
            payload={},
        )
        # Should flag missing baseline renal context without inventing creatinine or eGFR
        self.assertGreater(len(findings), 0)
        inputs = findings[0].inputs
        self.assertEqual(inputs.get("renal_context"), "missing")
        self.assertNotIn("latest_renal_lab", inputs)

    def test_c2_unknown_drug_safe_pair(self):
        """C.2: Unknown drug or safe non-interacting pair -> zero safety findings."""
        p = Patient(patient_identifier="VAL-NEG-002", name="Safe Pair Patient", sex="F")
        self.session.add(p)
        self.session.commit()
        self.session.add(Medication(patient_id=p.id, drug_name="acetaminophen", dose="500", dose_unit="mg", status="active"))
        self.session.commit()

        findings, resolutions = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="MEDICATION_PRESCRIBED",
            payload={"drug_name": "metformin", "dose": "500mg"},
            new_medication_name="metformin",
        )
        self.assertEqual(len(findings), 0)
        self.assertEqual(len(resolutions), 0)

    def test_c3_inactive_rule(self):
        """C.3: Inactive rule must be strictly filtered out and never trigger."""
        custom_pack = RulePack(
            pack_name="test_inactive",
            version="1.0.0",
            rules=[
                SafetyRule(
                    rule_id="INACTIVE-001",
                    rule_type="drug_interaction",
                    drug_a="drug_x",
                    drug_b="drug_y",
                    source="TEST",
                    source_version="1.0.0",
                    evidence_id="TEST-EV",
                    evidence_text="Inactive interaction",
                    status="inactive",
                )
            ],
        )
        engine = SafetyResolutionEngine(rule_context=custom_pack)
        active_rules = engine.get_active_rules()
        self.assertFalse(any(getattr(r, "rule_id", None) == "INACTIVE-001" for r in active_rules))

    def test_c4_malformed_event_resilience(self):
        """C.4: Malformed event payload (empty, null values, corrupted types) does not crash or corrupt state."""
        p = Patient(patient_identifier="VAL-NEG-004", name="Malformed Event Patient", sex="M")
        self.session.add(p)
        self.session.commit()

        # Ingest malformed events safely
        findings_empty, _ = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="UNKNOWN_EVENT",
            payload=None,
        )
        self.assertEqual(len(findings_empty), 0)

        findings_corrupt, _ = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="LAB_RESULT_RECORDED",
            payload={"test_name": "", "value": None},
        )
        self.assertEqual(len(findings_corrupt), 0)

    def test_c5_duplicate_unchanged_event(self):
        """C.5: Repeated identical event produces no duplicate finding rows."""
        p = Patient(patient_identifier="VAL-NEG-005", name="Deduplication Patient", sex="F")
        self.session.add(p)
        self.session.commit()
        self.session.add(Medication(patient_id=p.id, drug_name="lisinopril", dose="20", dose_unit="mg", status="active"))
        self.session.commit()

        f_first, _ = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="MEDICATION_PRESCRIBED",
            payload={"drug_name": "enalapril"},
            new_medication_name="enalapril",
        )
        self.assertEqual(len(f_first), 1)

        # Ingest identical event again
        f_second, _ = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="MEDICATION_PRESCRIBED",
            payload={"drug_name": "enalapril"},
            new_medication_name="enalapril",
        )
        self.assertEqual(len(f_second), 0, "Repeated unchanged event must produce zero duplicate findings")

    def test_c6_changed_relevant_context(self):
        """C.6: When relevant clinical context changes, finding is re-evaluated and updated in-place."""
        p = Patient(patient_identifier="VAL-NEG-006", name="Re-evaluation Patient", sex="M")
        self.session.add(p)
        self.session.commit()
        self.session.add(Medication(patient_id=p.id, drug_name="warfarin", dose="5", dose_unit="mg", status="active"))
        self.session.add(Medication(patient_id=p.id, drug_name="fluconazole", dose="200", dose_unit="mg", status="active"))
        self.session.add(Lab(patient_id=p.id, test_name="INR", value="2.0"))
        self.session.commit()

        # Initial evaluation
        f_init, _ = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="MEDICATION_REVIEWED",
            payload={},
        )
        self.assertEqual(len(f_init), 1)
        initial_id = f_init[0].id
        initial_trend = f_init[0].inputs.get("inr_trend")

        # Now context changes: new higher lab INR 4.2
        f_reeval, _ = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="LAB_RESULT_RECORDED",
            payload={"test_name": "INR", "value": "4.2"},
        )
        self.assertEqual(len(f_reeval), 1)
        self.assertEqual(f_reeval[0].id, initial_id, "Finding updated in-place")
        self.assertEqual(f_reeval[0].inputs.get("inr_value"), "4.2")

    # =========================================================================
    # CATEGORY D: SAFETY GUARANTEES
    # =========================================================================

    def test_d1_no_invented_clinical_values(self):
        """D.1: Preserves existing medication doses and frequencies; never invents unvalidated doses."""
        candidate = ResolutionCandidate(
            finding_id=99,
            action_type="monitor",
            description="Hold and monitor INR",
            rationale="CYP2C9 inhibition",
            source="AEGIS_HACKATHON_DEMO",
            requires_cosign=True,
        )
        # Without existing medication details
        sim1 = self.resolution_engine.create_simulated_order(candidate=candidate, patient_id=1)
        self.assertIsNone(sim1.dose)
        self.assertIsNone(sim1.route)
        self.assertIsNone(sim1.frequency)

        # With existing medication details: strictly preserved
        existing_med = {"drug_name": "warfarin", "dose": "5", "dose_unit": "mg", "route": "oral", "frequency": "daily"}
        sim2 = self.resolution_engine.create_simulated_order(candidate=candidate, existing_medication=existing_med, patient_id=1)
        self.assertEqual(sim2.dose, "5")
        self.assertEqual(sim2.dose_unit, "mg")
        self.assertEqual(sim2.route, "oral")
        self.assertEqual(sim2.frequency, "daily")

    def test_d2_no_automatic_medication_execution(self):
        """D.2: Simulated orders enforce auto_execute=False and cannot be constructed with auto_execute=True."""
        with self.assertRaises(Exception):
            SimulatedOrder(
                simulation_id="SIM-TEST",
                finding_id=1,
                drug_name="warfarin",
                proposed_action="hold",
                action_type="drug_interaction",
                rationale="test",
                source="test",
                auto_execute=True,  # Prohibited by safety validator
            )

    def test_d3_mandatory_cosign_enforcement(self):
        """D.3: requires_cosign must always be True; cannot be set to False."""
        with self.assertRaises(Exception):
            ResolutionCandidate(
                finding_id=1,
                action_type="monitor",
                description="test",
                rationale="test",
                source="test",
                requires_cosign=False,  # Prohibited by safety validator
            )

        with self.assertRaises(Exception):
            SimulatedOrder(
                simulation_id="SIM-TEST",
                finding_id=1,
                drug_name="warfarin",
                proposed_action="hold",
                action_type="drug_interaction",
                rationale="test",
                source="test",
                requires_cosign=False,  # Prohibited by safety validator
            )

    def test_d4_deterministic_safety_decisions(self):
        """D.4: Safety decisions are strictly deterministic rule matches, never stochastic ML."""
        p = Patient(patient_identifier="VAL-DET-001", name="Deterministic Patient", sex="M")
        self.session.add(p)
        self.session.commit()
        self.session.add(Medication(patient_id=p.id, drug_name="enoxaparin", dose="40", dose_unit="mg", status="active"))
        self.session.add(Lab(patient_id=p.id, test_name="Creatinine", value="1.0"))
        self.session.commit()

        # Run 5 times: every run must yield identical rule_id, status, and candidate count
        for _ in range(5):
            findings, resolutions = self.event_service.process_event_with_resolutions(
                db=self.session,
                patient_id=p.id,
                event_type="LAB_RESULT_RECORDED",
                payload={"test_name": "Creatinine", "value": "2.4"},
            )
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].rule_id, "AEGIS-DEMO-002-ENOXAPARIN-RENAL")
            self.assertEqual(resolutions[0].status, "actionable")
            self.assertEqual(len(resolutions[0].ranked_candidates), 1)

    def test_d5_ollama_never_makes_clinical_decisions(self):
        """D.5: Ollama is strictly an optional wording layer; clinical decisions are made by rule logic."""
        # Explanations generated without Ollama client produce complete deterministic explanation
        f = Finding(
            patient_id=1,
            rule_id="AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
            severity="review_required",
            title="Warfarin + Fluconazole Interaction",
            description="CYP2C9 inhibition",
            action="review/hold/monitor",
            inputs={"drug_a": "warfarin", "drug_b": "fluconazole"},
            trace={"source": "AEGIS_HACKATHON_DEMO", "evidence_id": "AEGIS-DEMO-EV-001"},
        )
        report = self.explicator.explicate(f)
        self.assertFalse(report.is_llm_enhanced)
        self.assertIn("warfarin", report.full_text.lower())
        self.assertIn("fluconazole", report.full_text.lower())
        self.assertEqual(report.evidence_source, "AEGIS_HACKATHON_DEMO")

    # =========================================================================
    # CATEGORY E: OFFLINE GUARANTEES
    # =========================================================================

    def test_e1_zero_external_network_calls(self):
        """E.1: Entire pipeline runs completely offline with no network socket connections."""
        # Mock socket.create_connection to fail if any network access is attempted
        original_connect = socket.socket.connect

        def forbidden_connect(*args, **kwargs):
            raise RuntimeError("CRITICAL ERROR: External network call attempted in offline mode!")

        socket.socket.connect = forbidden_connect
        try:
            # Run complete Case 1 pipeline
            p = Patient(patient_identifier="VAL-NET-001", name="Offline Patient", sex="F")
            self.session.add(p)
            self.session.commit()
            self.session.add(Medication(patient_id=p.id, drug_name="warfarin", dose="5", dose_unit="mg", status="active"))
            self.session.add(Lab(patient_id=p.id, test_name="INR", value="2.1"))
            self.session.commit()

            f, r = self.event_service.process_event_with_resolutions(
                db=self.session,
                patient_id=p.id,
                event_type="MEDICATION_PRESCRIBED",
                payload={"drug_name": "fluconazole"},
                new_medication_name="fluconazole",
            )
            self.assertEqual(len(f), 1)
            self.assertTrue(self.audit_ledger.verify_chain().is_valid)
        finally:
            socket.socket.connect = original_connect

    def test_e2_ollama_optional_and_deterministic_fallback(self):
        """E.2: System functions completely without Ollama; deterministic fallback is guaranteed."""
        exp_svc = ExplicatorService(ollama_client=None)
        f = Finding(
            patient_id=1,
            rule_id="AEGIS-DEMO-002-ENOXAPARIN-RENAL",
            severity="review_required",
            title="Enoxaparin Renal Finding",
            description="Declining renal function",
            action="review/hold/monitor",
            inputs={"drug_name": "enoxaparin", "renal_context": "declining"},
            trace={"source": "AEGIS_HACKATHON_DEMO", "evidence_id": "AEGIS-DEMO-EV-002"},
        )
        report = exp_svc.explicate(f)
        self.assertIsNotNone(report)
        self.assertFalse(report.is_llm_enhanced)
        self.assertIn("enoxaparin", report.full_text.lower())

    # =========================================================================
    # CATEGORY F: INTEGRITY AND TAMPER DETECTION
    # =========================================================================

    def test_f1_audit_hash_chain_verification_and_tamper_detection(self):
        """F.1: Audit ledger hash-chain verifies cleanly and immediately detects payload tampering."""
        rec1 = self.audit_ledger.append_event(actor="event_service", event_type="TEST_EVENT_1", payload={"data": 100})
        rec2 = self.audit_ledger.append_event(actor="risk_detector", event_type="TEST_EVENT_2", payload={"data": 200})

        # Chain valid initially
        v1 = self.audit_ledger.verify_chain()
        self.assertTrue(v1.is_valid)
        self.assertEqual(v1.total_records, 2)

        # Simulate tampering on record 1 in SQLite
        with self.audit_ledger._connection() as conn:
            conn.execute("UPDATE audit_ledger SET payload = '{\"data\":999}' WHERE id = ?", (rec1.id,))
            conn.commit()

        # Verification must now FAIL and pinpoint tampered ID
        v2 = self.audit_ledger.verify_chain()
        self.assertFalse(v2.is_valid)
        self.assertEqual(v2.tampered_record_id, rec1.id)
        self.assertGreater(len(v2.errors), 0)

    def test_f2_provenance_continuity(self):
        """F.2: Provenance continuity is preserved across detection, resolution, simulated order, and audit."""
        p = Patient(patient_identifier="VAL-PROV-001", name="Provenance Patient", sex="M")
        self.session.add(p)
        self.session.commit()
        self.session.add(Medication(patient_id=p.id, drug_name="warfarin", dose="5", dose_unit="mg", status="active"))
        self.session.add(Lab(patient_id=p.id, test_name="INR", value="3.5"))
        self.session.commit()

        findings, resolutions = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="MEDICATION_PRESCRIBED",
            payload={"drug_name": "fluconazole"},
            new_medication_name="fluconazole",
        )
        f = findings[0]
        res = resolutions[0]
        so = res.simulated_order

        # Traceability assertions
        self.assertEqual(f.rule_id, "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertEqual(res.rule_id, f.rule_id)
        self.assertEqual(so.rule_id, f.rule_id)
        self.assertEqual(so.evidence_id, "AEGIS-DEMO-EV-001")
        self.assertEqual(so.source, "AEGIS_HACKATHON_DEMO")

        # Audit ledger assertion
        events = self.audit_ledger.get_events()
        det_events = [e for e in events if e.event_type == "RISK_FINDING_DETECTED"]
        self.assertGreater(len(det_events), 0)
        self.assertEqual(det_events[0].payload.get("rule_id"), f.rule_id)
        self.assertEqual(det_events[0].payload.get("evidence_id"), "AEGIS-DEMO-EV-001")

    def test_f3_production_database_untouched(self):
        """F.3: Verify production aegis_rx.db file is NEVER modified during tests."""
        prod_db_path = PROJECT_ROOT / "aegis_rx.db"
        if prod_db_path.exists():
            mod_time_before = os.path.getmtime(prod_db_path)
            # Execute pipeline test
            self.test_b1_case1_warfarin_fluconazole_inr()
            mod_time_after = os.path.getmtime(prod_db_path)
            self.assertEqual(mod_time_before, mod_time_after, "Production aegis_rx.db was modified during testing!")

    # =========================================================================
    # CATEGORY G: DETERMINISM
    # =========================================================================

    def test_g1_repeated_identical_executions_yield_equivalent_results(self):
        """G.1: Repeated identical executions produce identical safety decisions and simulation IDs."""
        p = Patient(patient_identifier="VAL-DET-REPEAT", name="Repeat Patient", sex="F")
        self.session.add(p)
        self.session.commit()
        self.session.add(Medication(patient_id=p.id, drug_name="enoxaparin", dose="40", dose_unit="mg", status="active"))
        self.session.add(Lab(patient_id=p.id, test_name="Creatinine", value="1.0"))
        self.session.commit()

        # Run 1
        f1, r1 = self.event_service.process_event_with_resolutions(
            db=self.session,
            patient_id=p.id,
            event_type="LAB_RESULT_RECORDED",
            payload={"test_name": "Creatinine", "value": "2.4"},
        )

        # Isolated re-run with fresh environment
        env2_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=env2_engine)
        Session2 = sessionmaker(bind=env2_engine)
        s2 = Session2()
        p2 = Patient(patient_identifier="VAL-DET-REPEAT", name="Repeat Patient", sex="F")
        s2.add(p2)
        s2.commit()
        s2.add(Medication(patient_id=p2.id, drug_name="enoxaparin", dose="40", dose_unit="mg", status="active"))
        s2.add(Lab(patient_id=p2.id, test_name="Creatinine", value="1.0"))
        s2.commit()

        svc2 = MedicationEventService(
            knowledge_service=self.knowledge_service,
            resolution_engine=self.resolution_engine,
            rule_context=self.resolution_engine,
            explicator=self.explicator,
            audit_ledger=self.audit_ledger,
        )
        f2, r2 = svc2.process_event_with_resolutions(
            db=s2,
            patient_id=p2.id,
            event_type="LAB_RESULT_RECORDED",
            payload={"test_name": "Creatinine", "value": "2.4"},
        )

        self.assertEqual(f1[0].rule_id, f2[0].rule_id)
        self.assertEqual(f1[0].severity, f2[0].severity)
        self.assertEqual(r1[0].status, r2[0].status)
        self.assertEqual(len(r1[0].ranked_candidates), len(r2[0].ranked_candidates))
        self.assertEqual(r1[0].simulated_order.simulation_id, r2[0].simulated_order.simulation_id)
        s2.close()


if __name__ == "__main__":
    unittest.main()
