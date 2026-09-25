import unittest
from datetime import datetime
from pydantic import ValidationError

from agents.resolution import (
    ResolutionCandidate,
    ResolutionPipelineResult,
    ResolutionResult,
    SafetyResolutionEngine,
    SimulatedOrder,
)
from agents.risk.schemas import Finding
from agents.rules.schemas import RulePack, SafetyRule
from models.models import Order


class TestStep40SafetyResolutionHardening(unittest.TestCase):
    """
    Isolated unit and integration tests for STEP 40 — Safety Resolution Hardening:
    1. Multiple valid candidates.
    2. Candidate ranking determinism.
    3. Duplicate candidates.
    4. Invalid/placeholder candidates.
    5. Conflicting candidate guidance.
    6. Missing patient medication attributes.
    7. Cosign enforcement.
    8. No automatic execution.
    9. Provenance preservation.
    """

    def setUp(self):
        self.rule_1 = SafetyRule(
            rule_id="RULE-HYPERKALEMIA-MONITOR",
            rule_type="lab_monitoring",
            drug_a="lisinopril",
            drug_b="spironolactone",
            source="DDInter",
            source_version="v2.1",
            evidence_id="EVID-DDI-HK-001",
            evidence_text="Concomitant use may result in hyperkalemia.",
            severity="Major",
            action="Monitor serum potassium within 48 to 72 hours of co-administration.",
            status="active",
        )
        self.rule_2 = SafetyRule(
            rule_id="RULE-HYPERKALEMIA-HOLD",
            rule_type="drug_interaction",
            drug_a="lisinopril",
            drug_b="spironolactone",
            source="openFDA",
            source_version="v3.0",
            evidence_id="EVID-FDA-HK-002",
            evidence_text="Hyperkalemia risk. Discontinue or avoid spironolactone in severe renal impairment.",
            severity="Major",
            action="Hold spironolactone if baseline potassium > 5.0 mEq/L.",
            status="active",
        )
        self.rule_pack = RulePack(
            pack_name="Step40HardeningPack",
            version="1.0.0",
            rules=[self.rule_1, self.rule_2],
        )
        self.engine = SafetyResolutionEngine(rule_context=self.rule_pack)

    def test_1_multiple_valid_candidates(self):
        """
        Verify that multiple valid, distinct candidates from validated rules
        are all handled and preserved in raw, filtered, and ranked stages.
        """
        finding = Finding(
            rule_id="RULE-HYPERKALEMIA-COMBINED",
            severity="Major",
            title="Lisinopril + Spironolactone Concurrent Therapy",
            description="Hyperkalemia risk with dual potassium-sparing agents.",
            action=None,
            inputs={"drug_a": "lisinopril", "drug_b": "spironolactone", "patient_id": 101},
            trace={
                "matched_pair": ["lisinopril", "spironolactone"],
                "source": "DDInter",
            },
            source="DDInter",
        )

        pipeline_result = self.engine.resolve_finding_pipeline(finding)

        self.assertEqual(pipeline_result.status, "actionable")
        self.assertGreaterEqual(len(pipeline_result.raw_candidates), 2)
        self.assertGreaterEqual(len(pipeline_result.safety_filtered_candidates), 2)
        self.assertGreaterEqual(len(pipeline_result.ranked_candidates), 2)

        # Ensure candidates are distinct valid clinical actions
        actions = [c.description for c in pipeline_result.ranked_candidates]
        self.assertIn("Monitor serum potassium within 48 to 72 hours of co-administration.", actions)
        self.assertIn("Hold spironolactone if baseline potassium > 5.0 mEq/L.", actions)

        # Top candidate was converted to simulated order
        self.assertIsNotNone(pipeline_result.simulated_order)
        self.assertEqual(pipeline_result.simulated_order.proposed_action, pipeline_result.ranked_candidates[0].description)

    def test_2_candidate_ranking_determinism(self):
        """
        Verify that candidate ranking is 100% deterministic regardless of input
        ordering or candidate shuffling.
        """
        cand_a = ResolutionCandidate(
            finding_id="FIND-100",
            action_type="monitor",
            description="Monitor serum potassium level",
            rationale="Rationale A",
            source="DDInter",
            rule_id="RULE-A",
            evidence_id="EV-A",
            requires_cosign=True,
        )
        cand_b = ResolutionCandidate(
            finding_id="FIND-100",
            action_type="avoid",
            description="Avoid concomitant administration",
            rationale="Rationale B",
            source="openFDA",
            rule_id="RULE-B",
            evidence_id="EV-B",
            requires_cosign=True,
        )
        cand_c = ResolutionCandidate(
            finding_id="FIND-100",
            action_type="adjust",
            description="Adjust dose of spironolactone",
            rationale="Rationale C",
            source="DDInter",
            rule_id="RULE-C",
            evidence_id="EV-C",
            requires_cosign=True,
        )

        # Rank in order [A, B, C]
        ranked_1 = self.engine.rank_candidates([cand_a, cand_b, cand_c], finding_severity="Major")
        # Rank in reverse order [C, B, A]
        ranked_2 = self.engine.rank_candidates([cand_c, cand_b, cand_a], finding_severity="Major")
        # Rank in mixed order [B, C, A]
        ranked_3 = self.engine.rank_candidates([cand_b, cand_c, cand_a], finding_severity="Major")

        # Deterministic ranking must produce identical order and priority scores
        order_1 = [c.description for c in ranked_1]
        order_2 = [c.description for c in ranked_2]
        order_3 = [c.description for c in ranked_3]

        self.assertEqual(order_1, order_2)
        self.assertEqual(order_2, order_3)

        # "avoid" has highest urgency keyword weight (+30) > "adjust" (+20) > "monitor" (+10)
        self.assertEqual(order_1[0], "Avoid concomitant administration")
        self.assertEqual(order_1[1], "Adjust dose of spironolactone")
        self.assertEqual(order_1[2], "Monitor serum potassium level")

        # Tie-breaker determinism: when scores are identical
        cand_tie_1 = ResolutionCandidate(
            finding_id="FIND-TIE",
            action_type="monitor",
            description="Monitor renal panel",
            rationale="Rationale",
            source="SourceA",
            rule_id="RULE-1",
            requires_cosign=True,
        )
        cand_tie_2 = ResolutionCandidate(
            finding_id="FIND-TIE",
            action_type="monitor",
            description="Monitor serum creatinine",
            rationale="Rationale",
            source="SourceB",
            rule_id="RULE-2",
            requires_cosign=True,
        )
        tie_ranked_fwd = self.engine.rank_candidates([cand_tie_1, cand_tie_2], finding_severity="Major")
        tie_ranked_rev = self.engine.rank_candidates([cand_tie_2, cand_tie_1], finding_severity="Major")
        self.assertEqual(
            [c.description for c in tie_ranked_fwd],
            [c.description for c in tie_ranked_rev],
        )

    def test_3_duplicate_candidates(self):
        """
        Verify that duplicate candidate actions are deterministically deduplicated.
        """
        cand1 = ResolutionCandidate(
            finding_id="FIND-DUP-1",
            action_type="monitor",
            description="Monitor serum potassium within 48 hours",
            rationale="Rationale 1",
            source="DDInter",
            rule_id="RULE-1",
            requires_cosign=True,
        )
        cand2 = ResolutionCandidate(
            finding_id="FIND-DUP-1",
            action_type="monitor",
            description="Monitor serum potassium within 48 hours",
            rationale="Rationale 2 (duplicate)",
            source="DDInter",
            rule_id="RULE-1-DUP",
            requires_cosign=True,
        )
        cand3 = ResolutionCandidate(
            finding_id="FIND-DUP-1",
            action_type="MONITOR",
            description="  monitor serum potassium within 48 hours  ",
            rationale="Rationale 3 (case/whitespace duplicate)",
            source="DDInter",
            rule_id="RULE-1-CASE",
            requires_cosign=True,
        )

        filtered = self.engine.filter_candidate_actions([cand1, cand2, cand3])
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].description, "Monitor serum potassium within 48 hours")

    def test_4_invalid_placeholder_candidates(self):
        """
        Verify that invalid or placeholder candidate actions are strictly rejected
        and trigger human review.
        """
        placeholders = [
            "none", "null", "N/A", "no action", "unknown", "placeholder",
            "TBD", "todo", "pending", "not available", "unavailable",
            "none specified", "undefined", "void", "test", "pending clinical review",
            "no action needed", "no change", "none required", "blank", "   ", ""
        ]

        for ph in placeholders:
            invalid_cand = ResolutionCandidate(
                finding_id="FIND-PH",
                action_type="monitor",
                description=ph if ph else "   ",
                rationale="Rationale",
                source="DDInter",
                requires_cosign=True,
            ) if ph.strip() else None

            if invalid_cand:
                filtered = self.engine.filter_candidate_actions([invalid_cand])
                self.assertEqual(len(filtered), 0, f"Placeholder '{ph}' should be filtered out")

        # Test finding with placeholder action in pipeline
        finding_ph = Finding(
            rule_id="RULE-PH-TEST",
            severity="Major",
            title="Placeholder Action Finding",
            description="Risk finding with placeholder action",
            action="No action needed",
            inputs={"drug_a": "DrugA", "drug_b": "DrugB"},
            source="DDInter",
        )
        pipe_res = self.engine.resolve_finding_pipeline(finding_ph)
        self.assertEqual(pipe_res.status, "requires_human_review")
        self.assertIsNone(pipe_res.simulated_order)
        self.assertEqual(len(pipe_res.safety_filtered_candidates), 0)
        self.assertIn("review", pipe_res.review_reason.lower())

    def test_5_conflicting_candidate_guidance(self):
        """
        Verify that conflicting candidate guidance is deterministically detected
        and routed to human review with simulated order suppressed.
        """
        cand_stop = ResolutionCandidate(
            finding_id="FIND-CONFLICT",
            action_type="drug_interaction",
            description="Discontinue spironolactone immediately due to hyperkalemia risk.",
            rationale="Contraindicated",
            source="DDInter",
            rule_id="RULE-STOP",
            requires_cosign=True,
        )
        cand_continue = ResolutionCandidate(
            finding_id="FIND-CONFLICT",
            action_type="drug_interaction",
            description="Continue and maintain spironolactone; safe to administer with diet monitoring.",
            rationale="Permitted",
            source="openFDA",
            rule_id="RULE-CONT",
            requires_cosign=True,
        )

        has_conflict, reason = self.engine.detect_conflicting_guidance([cand_stop, cand_continue])
        self.assertTrue(has_conflict)
        self.assertIn("Contradictory directives", reason)

        # Pipeline must set status='requires_human_review' and simulated_order=None
        finding = Finding(
            rule_id="RULE-CONFLICT-FINDING",
            severity="Major",
            title="Conflicting Finding",
            description="Finding with conflicting guidance",
            action=None,
            inputs={"drug_a": "lisinopril", "drug_b": "spironolactone"},
            source="DDInter",
        )
        # Mock engine to return conflicting candidates
        orig_resolve = self.engine.resolve_finding
        try:
            self.engine.resolve_finding = lambda f, **kw: ResolutionResult(
                finding_id="RULE-CONFLICT-FINDING",
                status="actionable",
                candidates=[cand_stop, cand_continue],
            )
            pipe_res = self.engine.resolve_finding_pipeline(finding)
            self.assertEqual(pipe_res.status, "requires_human_review")
            self.assertIsNone(pipe_res.simulated_order)
            self.assertIn("Conflicting candidate guidance detected", pipe_res.review_reason)
        finally:
            self.engine.resolve_finding = orig_resolve

    def test_6_missing_patient_medication_attributes(self):
        """
        Verify that missing patient medication attributes remain None, never guessed,
        and only trusted non-placeholder attributes are preserved.
        """
        candidate = ResolutionCandidate(
            finding_id="FIND-MISSING-ATTRS",
            action_type="lab_monitoring",
            description="Monitor serum potassium within 48 hours.",
            rationale="Lisinopril + spironolactone interaction",
            source="DDInter",
            rule_id="RULE-DDI-001",
            source_version="v2.1",
            evidence_id="EVID-101",
            requires_cosign=True,
        )

        # 1. Medication with placeholders and missing fields
        untrusted_med = {
            "id": 88,
            "patient_id": 999,
            "drug_name": "Spironolactone",
            "dose": "None",          # Placeholder
            "dose_unit": "n/a",       # Placeholder
            "route": "  ",            # Whitespace
            "frequency": None,        # Missing
        }

        sim_order = self.engine.create_simulated_order(
            candidate=candidate,
            existing_medication=untrusted_med,
        )

        self.assertEqual(sim_order.drug_name, "Spironolactone")
        self.assertIsNone(sim_order.dose)
        self.assertIsNone(sim_order.dose_unit)
        self.assertIsNone(sim_order.route)
        self.assertIsNone(sim_order.frequency)

        # 2. No medication and no finding context -> all remain None
        sim_no_ctx = self.engine.create_simulated_order(candidate=candidate)
        self.assertIsNone(sim_no_ctx.patient_id)
        self.assertIsNone(sim_no_ctx.medication_id)
        self.assertIsNone(sim_no_ctx.dose)
        self.assertIsNone(sim_no_ctx.dose_unit)
        self.assertIsNone(sim_no_ctx.route)
        self.assertIsNone(sim_no_ctx.frequency)

        # 3. Trusted verified attributes preserved verbatim
        trusted_med = {
            "id": 99,
            "patient_id": 101,
            "drug_name": "Spironolactone 25mg",
            "dose": "25",
            "dose_unit": "mg",
            "route": "oral",
            "frequency": "daily",
        }
        sim_trusted = self.engine.create_simulated_order(
            candidate=candidate,
            existing_medication=trusted_med,
        )
        self.assertEqual(sim_trusted.dose, "25")
        self.assertEqual(sim_trusted.dose_unit, "mg")
        self.assertEqual(sim_trusted.route, "oral")
        self.assertEqual(sim_trusted.frequency, "daily")

    def test_7_cosign_enforcement(self):
        """
        Verify mandatory clinician cosign enforcement across candidate, simulated order,
        and pipeline schemas.
        """
        # Candidate requires_cosign must be True
        with self.assertRaises(ValidationError):
            ResolutionCandidate(
                finding_id="F-1",
                action_type="monitor",
                description="Monitor labs",
                rationale="Rationale",
                source="DDInter",
                requires_cosign=False,
            )

        # SimulatedOrder requires_cosign must be True
        with self.assertRaises(ValidationError):
            SimulatedOrder(
                simulation_id="SIM-FAIL",
                finding_id="F-1",
                drug_name="Lisinopril",
                proposed_action="Monitor labs",
                action_type="monitor",
                rationale="Rationale",
                source="DDInter",
                requires_cosign=False,
            )

        # ResolutionPipelineResult requires_cosign must be True
        with self.assertRaises(ValidationError):
            ResolutionPipelineResult(
                finding_id="F-1",
                status="actionable",
                requires_cosign=False,
            )

        # SimulatedOrder status must be pending_cosign
        with self.assertRaises(ValidationError):
            SimulatedOrder(
                simulation_id="SIM-FAIL-STATUS",
                finding_id="F-1",
                drug_name="Lisinopril",
                proposed_action="Monitor labs",
                action_type="monitor",
                rationale="Rationale",
                source="DDInter",
                status="completed",  # Not allowed
            )

    def test_8_no_automatic_execution(self):
        """
        Verify that simulated orders cannot be auto-executed or bypass simulation state.
        """
        # auto_execute=True is forbidden
        with self.assertRaises(ValidationError):
            SimulatedOrder(
                simulation_id="SIM-FAIL-AUTO",
                finding_id="F-1",
                drug_name="Lisinopril",
                proposed_action="Hold dose",
                action_type="hold",
                rationale="Rationale",
                source="DDInter",
                auto_execute=True,
            )

        # is_simulated=False is forbidden
        with self.assertRaises(ValidationError):
            SimulatedOrder(
                simulation_id="SIM-FAIL-NOT-SIM",
                finding_id="F-1",
                drug_name="Lisinopril",
                proposed_action="Hold dose",
                action_type="hold",
                rationale="Rationale",
                source="DDInter",
                is_simulated=False,
            )

        # to_orm_order produces an Order with pending_cosign status (never executed)
        valid_sim = SimulatedOrder(
            simulation_id="SIM-VALID",
            finding_id="F-1",
            patient_id=101,
            medication_id=202,
            drug_name="Lisinopril",
            proposed_action="Hold dose",
            action_type="hold",
            dose="10",
            dose_unit="mg",
            rationale="Rationale",
            source="DDInter",
        )
        orm_order = valid_sim.to_orm_order()
        self.assertIsInstance(orm_order, Order)
        self.assertEqual(orm_order.status, "pending_cosign")
        self.assertEqual(orm_order.drug_name, "Lisinopril")
        self.assertEqual(orm_order.dose, "10")

    def test_9_provenance_preservation(self):
        """
        Verify that rule_id, source, source_version, and evidence_id are completely
        preserved from the safety rule all the way to candidate and simulated order.
        """
        finding = Finding(
            rule_id="RULE-HYPERKALEMIA-MONITOR",
            severity="Major",
            title="Lisinopril + Spironolactone",
            description="Hyperkalemia risk",
            action=None,
            inputs={"drug_a": "lisinopril", "drug_b": "spironolactone", "patient_id": 101},
            trace={
                "rule_id": "RULE-HYPERKALEMIA-MONITOR",
                "source": "DDInter",
                "source_version": "v2.1",
                "evidence_id": "EVID-DDI-HK-001",
                "matched_pair": ["lisinopril", "spironolactone"],
            },
            source="DDInter",
        )

        pipe_res = self.engine.resolve_finding_pipeline(finding)
        self.assertEqual(pipe_res.status, "actionable")
        self.assertGreaterEqual(len(pipe_res.ranked_candidates), 1)

        # Check candidate provenance for top candidate (RULE-HYPERKALEMIA-HOLD has higher clinical urgency)
        top_cand = pipe_res.ranked_candidates[0]
        self.assertEqual(top_cand.rule_id, "RULE-HYPERKALEMIA-HOLD")
        self.assertEqual(top_cand.source, "openFDA")
        self.assertEqual(top_cand.source_version, "v3.0")
        self.assertEqual(top_cand.evidence_id, "EVID-FDA-HK-002")

        # Check simulated order provenance strictly matches top candidate
        sim_order = pipe_res.simulated_order
        self.assertIsNotNone(sim_order)
        self.assertEqual(sim_order.rule_id, "RULE-HYPERKALEMIA-HOLD")
        self.assertEqual(sim_order.source, "openFDA")
        self.assertEqual(sim_order.source_version, "v3.0")
        self.assertEqual(sim_order.evidence_id, "EVID-FDA-HK-002")

        # Check provenance preservation on candidate 2 as well
        cand_2 = pipe_res.ranked_candidates[1]
        self.assertEqual(cand_2.rule_id, "RULE-HYPERKALEMIA-MONITOR")
        self.assertEqual(cand_2.source, "DDInter")
        self.assertEqual(cand_2.source_version, "v2.1")
        self.assertEqual(cand_2.evidence_id, "EVID-DDI-HK-001")


if __name__ == "__main__":
    unittest.main()
