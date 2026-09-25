import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.audit.ledger import AuditLedger
from agents.explicator.service import ExplicatorService
from agents.resolution.resolver import SafetyResolutionEngine
from agents.resolution.schemas import (
    ResolutionCandidate,
    ResolutionPipelineResult,
    SimulatedOrder,
)
from agents.risk.detector import RiskDetector
from agents.risk.schemas import Finding
from agents.rules.schemas import SafetyRule
from engine.event_service import MedicationEventService
from models.models import Base, Patient, Medication, Lab


class TestStep42ClinicalTraceabilityProvenance(unittest.TestCase):
    """
    Isolated tests for STEP 42: Clinical Traceability & Provenance Hardening.
    Verifies end-to-end provenance preservation across:
    Finding -> Rule/Evidence -> Risk inputs/trace -> Resolution candidates ->
    Selected/ranked action -> Explanation -> SimulatedOrder -> Audit events.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.temp_dir.name) / "test_audit_ledger.db"
        self.ledger = AuditLedger(db_path=str(self.ledger_path))
        self.explicator = ExplicatorService()
        self.resolver = SafetyResolutionEngine(
            explicator=self.explicator,
            audit_ledger=self.ledger,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_1_finding_provenance(self):
        """1. Every safety Finding must preserve rule_id, source, source_version, evidence_id, and inputs/trace."""
        finding = Finding(
            rule_id="AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
            severity="review_required",
            title="Warfarin + Fluconazole with rising INR",
            description="Interaction detected between warfarin and fluconazole.",
            action="review/hold/monitor; evaluate safer alternative",
            inputs={"drug_a": "warfarin", "drug_b": "fluconazole", "inr_value": "3.4"},
            trace={
                "rule_id": "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
                "evidence_id": "AEGIS-DEMO-EV-001",
                "source": "AEGIS_HACKATHON_DEMO",
                "source_version": "1.0.0",
                "matched_pair": ["fluconazole", "warfarin"],
            },
            source="AEGIS_HACKATHON_DEMO",
        )

        self.assertEqual(finding.rule_id, "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertEqual(finding.source, "AEGIS_HACKATHON_DEMO")
        self.assertEqual(finding.source_version, "1.0.0")
        self.assertEqual(finding.evidence_id, "AEGIS-DEMO-EV-001")
        self.assertEqual(finding.inputs["inr_value"], "3.4")
        self.assertEqual(finding.trace["matched_pair"], ["fluconazole", "warfarin"])

    def test_2_provenance_propagation_into_resolution(self):
        """2. Resolution candidate actions must retain originating Finding, rule_id, source, version, and evidence_id."""
        finding = Finding(
            rule_id="AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
            severity="review_required",
            title="Warfarin + Fluconazole with rising INR",
            description="Interaction detected between warfarin and fluconazole.",
            action="review/hold/monitor; evaluate safer alternative",
            inputs={"drug_a": "warfarin", "drug_b": "fluconazole", "patient_id": 10},
            trace={
                "rule_id": "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
                "evidence_id": "AEGIS-DEMO-EV-001",
                "source": "AEGIS_HACKATHON_DEMO",
                "source_version": "1.0.0",
                "matched_pair": ["fluconazole", "warfarin"],
            },
            source="AEGIS_HACKATHON_DEMO",
        )

        res_result = self.resolver.resolve_finding(finding)
        self.assertEqual(res_result.status, "actionable")
        self.assertGreater(len(res_result.candidates), 0)

        cand = res_result.candidates[0]
        self.assertEqual(cand.rule_id, "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertEqual(cand.source, "AEGIS_HACKATHON_DEMO")
        self.assertEqual(cand.source_version, "1.0.0")
        self.assertEqual(cand.evidence_id, "AEGIS-DEMO-EV-001")
        self.assertTrue(cand.requires_cosign)

        # Verify filtering and ranking preserve provenance
        filtered = self.resolver.filter_candidate_actions(res_result.candidates)
        self.assertEqual(filtered[0].rule_id, "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        ranked = self.resolver.rank_candidates(filtered, finding_severity="review_required")
        self.assertEqual(ranked[0].rule_id, "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertEqual(ranked[0].evidence_id, "AEGIS-DEMO-EV-001")

    def test_3_provenance_propagation_into_explanation(self):
        """3. Explanation must reference the same rule/evidence provenance."""
        finding = Finding(
            rule_id="AEGIS-DEMO-002-ENOXAPARIN-RENAL",
            severity="review_required",
            title="Enoxaparin with declining renal function",
            description="Declining renal function detected while receiving enoxaparin.",
            action="review/hold/monitor; evaluate dose adjustment",
            inputs={"drug_a": "enoxaparin", "patient_id": 20, "lab_count": 2},
            trace={
                "rule_id": "AEGIS-DEMO-002-ENOXAPARIN-RENAL",
                "evidence_id": "AEGIS-DEMO-EV-002",
                "source": "AEGIS_HACKATHON_DEMO",
                "source_version": "1.0.0",
                "matched_pair": ["enoxaparin"],
            },
            source="AEGIS_HACKATHON_DEMO",
        )

        report = self.explicator.explicate(finding)
        self.assertEqual(report.rule_id, "AEGIS-DEMO-002-ENOXAPARIN-RENAL")
        self.assertEqual(report.evidence_source, "AEGIS_HACKATHON_DEMO")
        self.assertEqual(report.evidence_id, "AEGIS-DEMO-EV-002")
        self.assertEqual(report.source_version, "1.0.0")
        self.assertIn("AEGIS-DEMO-EV-002", report.full_text)
        self.assertIn("AEGIS_HACKATHON_DEMO", report.full_text)

    def test_4_provenance_propagation_into_simulated_order(self):
        """4. SimulatedOrder must reference originating Finding, Resolution, rule_id, evidence_id, source."""
        candidate = ResolutionCandidate(
            finding_id="AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR",
            action_type="duplicate_therapy",
            description="duplicate therapy review",
            rationale="Multiple active ACE-inhibitors detected.",
            source="AEGIS_HACKATHON_DEMO",
            rule_id="AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR",
            source_version="1.0.0",
            evidence_id="AEGIS-DEMO-EV-003",
            requires_cosign=True,
        )

        sim_order = self.resolver.create_simulated_order(
            candidate=candidate,
            patient_id=42,
            existing_medication={"drug_name": "lisinopril", "dose": "20mg", "route": "oral"},
        )

        self.assertEqual(sim_order.finding_id, "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR")
        self.assertEqual(sim_order.rule_id, "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR")
        self.assertEqual(sim_order.source, "AEGIS_HACKATHON_DEMO")
        self.assertEqual(sim_order.source_version, "1.0.0")
        self.assertEqual(sim_order.evidence_id, "AEGIS-DEMO-EV-003")
        self.assertEqual(sim_order.resolution_id, "RES-AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR-duplicate_therapy")
        self.assertEqual(sim_order.drug_name, "lisinopril")
        self.assertEqual(sim_order.dose, "20mg")
        self.assertTrue(sim_order.requires_cosign)
        self.assertFalse(sim_order.auto_execute)
        self.assertTrue(sim_order.is_simulated)
        self.assertEqual(sim_order.status, "pending_cosign")

    def test_5_audit_payload_traceability(self):
        """5. Audit payloads must contain enough identifiers to reconstruct the lifecycle without storing sensitive raw PHI."""
        finding = Finding(
            rule_id="AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
            severity="review_required",
            title="Warfarin + Fluconazole with rising INR",
            description="Interaction detected.",
            action="review/hold/monitor; evaluate safer alternative",
            inputs={"drug_a": "warfarin", "drug_b": "fluconazole", "patient_id": 99},
            trace={
                "rule_id": "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
                "evidence_id": "AEGIS-DEMO-EV-001",
                "source": "AEGIS_HACKATHON_DEMO",
                "source_version": "1.0.0",
                "matched_pair": ["fluconazole", "warfarin"],
            },
            source="AEGIS_HACKATHON_DEMO",
        )

        pipeline_result = self.resolver.resolve_finding_pipeline(
            finding=finding,
            patient_id=99,
            existing_medication={"drug_name": "warfarin", "dose": "5mg"},
        )

        self.assertIsNotNone(pipeline_result.simulated_order)
        self.assertIsNotNone(pipeline_result.explanation)

        events = self.ledger.get_events()
        event_types = [e.event_type for e in events]
        self.assertIn("SAFETY_RESOLUTION_EVALUATED", event_types)
        self.assertIn("EXPLANATION_GENERATED", event_types)
        self.assertIn("SIMULATED_ORDER_CREATED", event_types)

        # Inspect resolution payload
        res_ev = [e for e in events if e.event_type == "SAFETY_RESOLUTION_EVALUATED"][0]
        self.assertEqual(res_ev.payload["rule_id"], "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertEqual(res_ev.payload["source"], "AEGIS_HACKATHON_DEMO")
        self.assertEqual(res_ev.payload["evidence_id"], "AEGIS-DEMO-EV-001")

        # Inspect order payload
        ord_ev = [e for e in events if e.event_type == "SIMULATED_ORDER_CREATED"][0]
        self.assertEqual(ord_ev.payload["rule_id"], "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertEqual(ord_ev.payload["resolution_id"], pipeline_result.simulated_order.resolution_id)
        self.assertEqual(ord_ev.payload["simulation_id"], pipeline_result.simulated_order.simulation_id)
        self.assertTrue(ord_ev.payload["requires_cosign"])
        self.assertFalse(ord_ev.payload["auto_execute"])

        # Verify hash chain integrity
        self.assertTrue(self.ledger.verify_chain().is_valid)

    def test_6_missing_provenance_handling(self):
        """6. Handle missing provenance explicitly; never fabricate source/evidence IDs."""
        finding = Finding(
            rule_id="CUSTOM-ALERT-999",
            severity="moderate",
            title="Custom Alert Without Evidence ID",
            description="Clinical note observation.",
            action=None,
            inputs={"drug_a": "aspirin"},
            trace={"source": "openFDA"},
            source="openFDA",
        )

        self.assertIsNone(finding.evidence_id)
        self.assertIsNone(finding.source_version)

        res_result = self.resolver.resolve_finding(finding)
        for c in res_result.candidates:
            self.assertIsNone(c.evidence_id)

        report = self.explicator.explicate(finding)
        self.assertIsNone(report.evidence_id)
        self.assertIsNone(report.source_version)
        self.assertNotIn("Evidence ID: CUSTOM-ALERT-999", report.full_text)

    def test_7_demo_rule_vs_validated_evidence_distinction(self):
        """7. Keep demo rules clearly distinguishable from validated external evidence."""
        demo_finding = Finding(
            rule_id="AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
            severity="review_required",
            title="Demo Rule Finding",
            description="Prototype rule finding.",
            action="review/hold/monitor; evaluate safer alternative",
            source="AEGIS_HACKATHON_DEMO",
            trace={"evidence_id": "AEGIS-DEMO-EV-001"},
        )
        self.assertTrue(demo_finding.is_demo_rule)

        validated_finding = Finding(
            rule_id="DDINTER-RULE-101",
            severity="major",
            title="Validated Finding",
            description="Clinically validated interaction from DDInter.",
            action="Monitor serum potassium within 48 to 72 hours.",
            source="DDInter",
            trace={"evidence_id": "DDINTER-EVID-555"},
        )
        self.assertFalse(validated_finding.is_demo_rule)

        demo_pipe = self.resolver.resolve_finding_pipeline(demo_finding)
        self.assertTrue(demo_pipe.is_demo_rule)
        if demo_pipe.simulated_order:
            self.assertTrue(demo_pipe.simulated_order.is_demo_rule)
        if demo_pipe.explanation:
            self.assertTrue(demo_pipe.explanation.is_demo_rule)

        val_pipe = self.resolver.resolve_finding_pipeline(validated_finding)
        self.assertFalse(val_pipe.is_demo_rule)
        if val_pipe.simulated_order:
            self.assertFalse(val_pipe.simulated_order.is_demo_rule)
        if val_pipe.explanation:
            self.assertFalse(val_pipe.explanation.is_demo_rule)

    def test_8_full_lifecycle_trace_reconstruction(self):
        """8. Full lifecycle trace reconstruction: Event -> Risk -> Finding -> Resolution -> Explanation -> Order -> Audit."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()

        patient = Patient(patient_identifier="TEST-PT-42", name="Test Patient", sex="M")
        session.add(patient)
        session.commit()

        # Add existing warfarin medication
        warf_med = Medication(
            patient_id=patient.id,
            drug_name="warfarin",
            dose="5",
            dose_unit="mg",
            route="oral",
            frequency="daily",
            status="active",
        )
        session.add(warf_med)

        # Add rising INR labs
        session.add(Lab(patient_id=patient.id, test_name="INR", value="2.1"))
        session.add(Lab(patient_id=patient.id, test_name="INR", value="3.5"))
        session.commit()

        from agents.knowledge import DrugNormalizer, KnowledgeService

        normalizer = DrugNormalizer()
        knowledge_svc = KnowledgeService(normalizer=normalizer)

        event_svc = MedicationEventService(
            knowledge_service=knowledge_svc,
            rule_context=self.resolver,
            resolution_engine=self.resolver,
            audit_ledger=self.ledger,
        )

        # Ingest fluconazole order event
        affected_findings, pipeline_results = event_svc.process_event_with_resolutions(
            db=session,
            patient_id=patient.id,
            event_type="MEDICATION_PRESCRIBED",
            payload={"drug_name": "fluconazole", "dose": "200mg"},
            new_medication_name="fluconazole",
        )

        self.assertGreater(len(affected_findings), 0)
        self.assertGreater(len(pipeline_results), 0)

        # Verify audit ledger recorded the complete ordered lifecycle
        events = self.ledger.get_events()
        event_types = [e.event_type for e in events]
        self.assertIn("PATIENT_EVENT_INGESTED", event_types)
        self.assertIn("RISK_FINDING_DETECTED", event_types)
        self.assertIn("SAFETY_RESOLUTION_EVALUATED", event_types)
        self.assertIn("EXPLANATION_GENERATED", event_types)
        self.assertIn("SIMULATED_ORDER_CREATED", event_types)

        # Verify cross-stage identifier linkage
        risk_ev = [e for e in events if e.event_type == "RISK_FINDING_DETECTED"][0]
        res_ev = [e for e in events if e.event_type == "SAFETY_RESOLUTION_EVALUATED"][0]
        exp_ev = [e for e in events if e.event_type == "EXPLANATION_GENERATED"][0]
        ord_ev = [e for e in events if e.event_type == "SIMULATED_ORDER_CREATED"][0]

        finding_db_id = risk_ev.payload["finding_id"]
        self.assertEqual(res_ev.payload["finding_id"], finding_db_id)
        self.assertEqual(exp_ev.payload["finding_id"], finding_db_id)
        self.assertEqual(ord_ev.payload["finding_id"], finding_db_id)

        # Rule ID linkage
        rule_id = risk_ev.payload["rule_id"]
        self.assertEqual(res_ev.payload["rule_id"], rule_id)
        self.assertEqual(exp_ev.payload["rule_id"], rule_id)
        self.assertEqual(ord_ev.payload["rule_id"], rule_id)

        # Evidence ID linkage
        ev_id = risk_ev.payload["evidence_id"]
        self.assertEqual(res_ev.payload["evidence_id"], ev_id)
        self.assertEqual(exp_ev.payload["evidence_id"], ev_id)
        self.assertEqual(ord_ev.payload["evidence_id"], ev_id)

        self.assertTrue(self.ledger.verify_chain().is_valid)

    def test_9_deterministic_repeated_execution(self):
        """9. Repeated identical pipeline execution produces byte-for-byte deterministic properties."""
        finding = Finding(
            rule_id="AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
            severity="review_required",
            title="Warfarin + Fluconazole with rising INR",
            description="Interaction detected between warfarin and fluconazole.",
            action="review/hold/monitor; evaluate safer alternative",
            inputs={"drug_a": "warfarin", "drug_b": "fluconazole", "inr_value": "3.4"},
            trace={
                "rule_id": "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
                "evidence_id": "AEGIS-DEMO-EV-001",
                "source": "AEGIS_HACKATHON_DEMO",
                "source_version": "1.0.0",
                "matched_pair": ["fluconazole", "warfarin"],
            },
            source="AEGIS_HACKATHON_DEMO",
        )

        res1 = self.resolver.resolve_finding_pipeline(
            finding=finding,
            patient_id=10,
            existing_medication={"drug_name": "warfarin", "dose": "5mg"},
        )
        res2 = self.resolver.resolve_finding_pipeline(
            finding=finding,
            patient_id=10,
            existing_medication={"drug_name": "warfarin", "dose": "5mg"},
        )

        # Verification of determinism
        self.assertEqual(res1.status, res2.status)
        self.assertEqual(res1.simulated_order.simulation_id, res2.simulated_order.simulation_id)
        self.assertEqual(res1.simulated_order.resolution_id, res2.simulated_order.resolution_id)
        self.assertEqual(res1.simulated_order.proposed_action, res2.simulated_order.proposed_action)
        self.assertEqual(res1.explanation.full_text, res2.explanation.full_text)
        self.assertEqual(len(res1.ranked_candidates), len(res2.ranked_candidates))
        self.assertEqual(res1.ranked_candidates[0].priority_score, res2.ranked_candidates[0].priority_score)


if __name__ == "__main__":
    unittest.main()
