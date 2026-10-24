import unittest
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


class TestResolutionHardening(unittest.TestCase):
    """
    Step 33B — Resolution Hardening tests:
    - Missing data handling
    - Inactive rules rejection
    - Duplicate findings deduplication
    - Unavailable validated actions enforcement
    - Strict prohibition on unvalidated doses and autonomous execution
    """

    def setUp(self):
        self.active_rule = SafetyRule(
            rule_id="RULE-ACTIVE-001",
            rule_type="drug_interaction",
            drug_a="lisinopril",
            drug_b="spironolactone",
            source="DDInter",
            source_version="v2.1",
            evidence_id="EVID-ACTIVE-001",
            evidence_text="Hyperkalemia risk.",
            severity="Major",
            action="Monitor serum potassium within 48 to 72 hours.",
            status="active",
        )
        self.inactive_rule = SafetyRule(
            rule_id="RULE-INACTIVE-002",
            rule_type="drug_interaction",
            drug_a="warfarin",
            drug_b="fluconazole",
            source="DDInter",
            source_version="v2.1",
            evidence_id="EVID-INACTIVE-002",
            evidence_text="INR elevation risk.",
            severity="Major",
            action="Discontinue fluconazole immediately.",
            status="inactive",  # INACTIVE RULE
        )
        self.draft_rule = SafetyRule(
            rule_id="RULE-DRAFT-003",
            rule_type="drug_interaction",
            drug_a="metformin",
            drug_b="contrast",
            source="openFDA",
            source_version="v1.0",
            evidence_id="EVID-DRAFT-003",
            evidence_text="Lactic acidosis risk.",
            severity="Critical",
            action="Hold metformin 48 hours prior to procedure.",
            status="draft",  # DRAFT RULE
        )
        self.deprecated_rule = SafetyRule(
            rule_id="RULE-DEPRECATED-004",
            rule_type="drug_interaction",
            drug_a="aspirin",
            drug_b="ibuprofen",
            source="DDInter",
            source_version="v1.0",
            evidence_id="EVID-DEP-004",
            evidence_text="GI toxicity.",
            severity="Moderate",
            action="Avoid concurrent use.",
            status="deprecated",  # DEPRECATED RULE
        )
        self.rule_pack = RulePack(
            pack_name="HardeningPack",
            version="1.0.0",
            rules=[
                self.active_rule,
                self.inactive_rule,
                self.draft_rule,
                self.deprecated_rule,
            ],
        )
        self.engine = SafetyResolutionEngine(rule_context=self.rule_pack)

    def test_inactive_draft_deprecated_rules_are_never_matched(self):
        """Test that inactive, draft, and deprecated rules are completely ignored."""
        # 1. Active rule matches
        active_finding = Finding(
            rule_id="RULE-ACTIVE-001",
            severity="Major",
            title="Active finding",
            description="Active risk",
            action=None,
            inputs={"drug_a": "lisinopril", "drug_b": "spironolactone"},
            source="DDInter",
        )
        res_active = self.engine.resolve_finding(active_finding)
        self.assertEqual(res_active.status, "actionable")
        self.assertEqual(len(res_active.candidates), 1)

        # 2. Inactive rule MUST NOT match
        inactive_finding = Finding(
            rule_id="RULE-INACTIVE-002",
            severity="Major",
            title="Inactive finding",
            description="Inactive risk",
            action=None,
            inputs={"drug_a": "warfarin", "drug_b": "fluconazole"},
            source="DDInter",
        )
        res_inactive = self.engine.resolve_finding(inactive_finding)
        self.assertEqual(res_inactive.status, "requires_human_review")
        self.assertEqual(len(res_inactive.candidates), 0)

        # 3. Draft rule MUST NOT match
        draft_finding = Finding(
            rule_id="RULE-DRAFT-003",
            severity="Critical",
            title="Draft finding",
            description="Draft risk",
            action=None,
            inputs={"drug_a": "metformin", "drug_b": "contrast"},
            source="openFDA",
        )
        res_draft = self.engine.resolve_finding(draft_finding)
        self.assertEqual(res_draft.status, "requires_human_review")
        self.assertEqual(len(res_draft.candidates), 0)

        # 4. Deprecated rule MUST NOT match
        dep_finding = Finding(
            rule_id="RULE-DEPRECATED-004",
            severity="Moderate",
            title="Deprecated finding",
            description="Deprecated risk",
            action=None,
            inputs={"drug_a": "aspirin", "drug_b": "ibuprofen"},
            source="DDInter",
        )
        res_dep = self.engine.resolve_finding(dep_finding)
        self.assertEqual(res_dep.status, "requires_human_review")
        self.assertEqual(len(res_dep.candidates), 0)

    def test_missing_data_graceful_handling(self):
        """Test engine handles None finding, empty inputs, corrupted trace, and missing fields without crashing."""
        # None finding
        res_none = self.engine.resolve_finding(None)
        self.assertEqual(res_none.status, "requires_human_review")
        self.assertEqual(len(res_none.candidates), 0)
        self.assertIn("No finding data provided", res_none.review_reason)

        # Finding with None inputs and None trace
        f_empty = {
            "id": None,
            "rule_id": None,
            "severity": None,
            "title": "Minimal",
            "description": None,
            "action": None,
            "inputs": None,
            "trace": None,
            "source": None,
        }
        res_empty = self.engine.resolve_finding(f_empty)
        self.assertEqual(res_empty.status, "requires_human_review")
        self.assertEqual(len(res_empty.candidates), 0)

        # Corrupted JSON string in inputs and trace
        f_corrupted = {
            "id": 999,
            "rule_id": "CORRUPT-JSON-001",
            "severity": "Moderate",
            "title": "Corrupt JSON finding",
            "description": "Test corrupt JSON",
            "action": None,
            "inputs": "INVALID_JSON_{{[",
            "trace": "{NOT_VALID_JSON}",
            "source": "Test",
        }
        pipe_res = self.engine.resolve_finding_pipeline(f_corrupted)
        self.assertEqual(pipe_res.status, "requires_human_review")
        self.assertIsNone(pipe_res.simulated_order)

    def test_unavailable_validated_actions_require_human_review(self):
        """Test that placeholder action strings (none, null, n/a, no action, unspecified) trigger human review."""
        placeholder_actions = ["none", "None", "NULL", "n/a", "no action", "unspecified", "unknown", "   "]

        for act in placeholder_actions:
            finding = Finding(
                rule_id=f"FINDING-PLACEHOLDER-{act}",
                severity="Major",
                title="Placeholder Action Finding",
                description="Risk reported without valid clinical action",
                action=act,
                inputs={"drug_a": "DrugA", "drug_b": "DrugB"},
                source="DDInter",
            )
            res = self.engine.resolve_finding(finding)
            self.assertEqual(
                res.status,
                "requires_human_review",
                f"Action '{act}' should trigger requires_human_review",
            )
            self.assertEqual(len(res.candidates), 0)
            self.assertIn("Mandatory clinical review", res.review_reason)

            # In pipeline, simulated_order MUST be None
            pipe_res = self.engine.resolve_finding_pipeline(finding)
            self.assertIsNone(pipe_res.simulated_order)

    def test_duplicate_findings_deduplication(self):
        """Test that batch resolution deduplicates identical finding records deterministically."""
        finding1 = Finding(
            rule_id="RULE-ACTIVE-001",
            severity="Major",
            title="Interaction 1",
            description="Hyperkalemia risk",
            action=None,
            inputs={"drug_a": "lisinopril", "drug_b": "spironolactone", "patient_id": 55},
            trace={"rule_id": "RULE-ACTIVE-001", "matched_pair": ["lisinopril", "spironolactone"]},
            source="DDInter",
        )
        finding2 = Finding(
            rule_id="RULE-ACTIVE-001",
            severity="Major",
            title="Interaction 1 Duplicate",
            description="Hyperkalemia risk",
            action=None,
            inputs={"drug_a": "lisinopril", "drug_b": "spironolactone", "patient_id": 55},
            trace={"rule_id": "RULE-ACTIVE-001", "matched_pair": ["lisinopril", "spironolactone"]},
            source="DDInter",
        )
        finding3 = Finding(
            rule_id="RULE-ACTIVE-001",
            severity="Major",
            title="Interaction 1 Triplicate",
            description="Hyperkalemia risk",
            action=None,
            inputs={"drug_a": "lisinopril", "drug_b": "spironolactone", "patient_id": 55},
            trace={"rule_id": "RULE-ACTIVE-001", "matched_pair": ["spironolactone", "lisinopril"]},
            source="DDInter",
        )

        results = self.engine.resolve_findings_pipeline([finding1, finding2, finding3])
        # Deduplication must reduce 3 identical findings to exactly 1
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].finding_id, "RULE-ACTIVE-001")
        self.assertEqual(results[0].status, "actionable")

    def test_never_invents_doses_or_thresholds(self):
        """Test simulated orders never synthesize unprovided doses, routes, or frequencies."""
        finding = Finding(
            rule_id="RULE-ACTIVE-001",
            severity="Major",
            title="Interaction",
            description="Hyperkalemia risk",
            action=None,
            inputs={"drug_a": "lisinopril", "drug_b": "spironolactone", "patient_id": 99},
            source="DDInter",
        )

        # 1. No medication context provided -> dose, route, frequency must be None
        pipe_res_no_med = self.engine.resolve_finding_pipeline(finding)
        sim_no_med = pipe_res_no_med.simulated_order
        self.assertIsNotNone(sim_no_med)
        self.assertIsNone(sim_no_med.dose)
        self.assertIsNone(sim_no_med.dose_unit)
        self.assertIsNone(sim_no_med.route)
        self.assertIsNone(sim_no_med.frequency)

        # 2. Existing medication with only dose provided -> route/frequency remain None
        partial_med = {
            "id": 10,
            "patient_id": 99,
            "drug_name": "Lisinopril 10mg",
            "dose": "10",
            "dose_unit": "mg",
            "route": None,
            "frequency": None,
        }
        pipe_res_partial = self.engine.resolve_finding_pipeline(finding, existing_medication=partial_med)
        sim_partial = pipe_res_partial.simulated_order
        self.assertEqual(sim_partial.dose, "10")
        self.assertEqual(sim_partial.dose_unit, "mg")
        self.assertIsNone(sim_partial.route)
        self.assertIsNone(sim_partial.frequency)

    def test_cosign_mandatory_enforcement(self):
        """Test candidate and simulated order schemas reject auto-execution and requires_cosign=False."""
        # Simulated order validation
        with self.assertRaises(ValidationError):
            SimulatedOrder(
                simulation_id="SIM-FAIL",
                finding_id="F-1",
                drug_name="Lisinopril",
                proposed_action="Hold dose",
                action_type="drug_interaction",
                rationale="Guidance",
                source="DDInter",
                requires_cosign=False,
            )

        with self.assertRaises(ValidationError):
            SimulatedOrder(
                simulation_id="SIM-FAIL",
                finding_id="F-1",
                drug_name="Lisinopril",
                proposed_action="Hold dose",
                action_type="drug_interaction",
                rationale="Guidance",
                source="DDInter",
                auto_execute=True,
            )


if __name__ == "__main__":
    unittest.main()
