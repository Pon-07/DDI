import unittest
from pydantic import ValidationError

from agents.resolution import (
    ResolutionCandidate,
    ResolutionResult,
    SafetyResolutionEngine,
)
from agents.risk.schemas import Finding
from agents.rules.schemas import RulePack, SafetyRule
from models import Finding as ORMFinding


class TestSafetyResolutionEngine(unittest.TestCase):
    """Unit tests for SafetyResolutionEngine and resolution schemas."""

    def setUp(self):
        self.validated_rule = SafetyRule(
            rule_id="RULE-DDI-001",
            rule_type="drug_interaction",
            drug_a="lisinopril",
            drug_b="spironolactone",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDI-EVID-101",
            evidence_text="Coadministration of Lisinopril and Spironolactone may produce severe hyperkalemia.",
            severity="Major",
            action="Monitor serum potassium and renal function closely if concomitant use is necessary.",
            status="active",
        )
        self.rule_pack = RulePack(
            pack_name="CardioSafetyRules",
            version="1.0.0",
            rules=[self.validated_rule],
        )
        self.engine = SafetyResolutionEngine(rule_context=self.rule_pack)

    def test_validated_action_available_from_rule_context(self):
        """Test that a finding matching a validated rule with an explicit action generates a candidate."""
        finding = Finding(
            rule_id="RULE-DDI-001",
            severity="Major",
            title="Lisinopril + Spironolactone Interaction",
            description="Coadministration of Lisinopril and Spironolactone may produce severe hyperkalemia.",
            action=None,  # Not in finding, but present in matched validated rule
            inputs={"drug_a": "Lisinopril", "drug_b": "Spironolactone", "patient_id": 101},
            trace={
                "rule_id": "RULE-DDI-001",
                "evidence_id": "DDI-EVID-101",
                "source": "DDInter",
                "source_version": "v2.1",
                "matched_pair": ["lisinopril", "spironolactone"],
            },
            source="DDInter",
        )

        result = self.engine.resolve_finding(finding)

        self.assertIsInstance(result, ResolutionResult)
        self.assertEqual(result.status, "actionable")
        self.assertEqual(len(result.candidates), 1)

        candidate = result.candidates[0]
        self.assertIsInstance(candidate, ResolutionCandidate)
        self.assertEqual(candidate.finding_id, "RULE-DDI-001")
        self.assertEqual(candidate.action_type, "drug_interaction")
        self.assertEqual(
            candidate.description,
            "Monitor serum potassium and renal function closely if concomitant use is necessary.",
        )
        self.assertEqual(candidate.source, "DDInter")
        self.assertEqual(candidate.evidence_id, "DDI-EVID-101")
        self.assertTrue(candidate.requires_cosign)
        self.assertIn("Validated action from DDInter", candidate.rationale)

    def test_validated_action_available_directly_in_finding(self):
        """Test that finding with its own validated action field produces a candidate without rule pack."""
        empty_engine = SafetyResolutionEngine()
        finding = Finding(
            rule_id="RULE-WARFARIN-001",
            severity="Major",
            title="Warfarin + Aspirin Bleeding Risk",
            description="Concomitant use increases upper GI bleeding risk.",
            action="Avoid concomitant use unless clinical benefit clearly outweighs the bleeding risk.",
            inputs={"drug_a": "Warfarin", "drug_b": "Aspirin"},
            trace={"evidence_id": "FDA-LBL-555", "source": "openFDA"},
            source="openFDA",
        )

        result = empty_engine.resolve_finding(finding)

        self.assertEqual(result.status, "actionable")
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.source, "openFDA")
        self.assertEqual(candidate.evidence_id, "FDA-LBL-555")
        self.assertEqual(
            candidate.description,
            "Avoid concomitant use unless clinical benefit clearly outweighs the bleeding risk.",
        )
        self.assertTrue(candidate.requires_cosign)

    def test_no_validated_action_requires_human_review(self):
        """Test that finding without validated action returns no candidates and requires human review."""
        rule_without_action = SafetyRule(
            rule_id="RULE-NO-ACTION",
            rule_type="drug_interaction",
            drug_a="metformin",
            drug_b="cimetidine",
            source="DDInter",
            source_version="v2.0",
            evidence_id="DDI-999",
            evidence_text="Cimetidine reduces metformin clearance.",
            severity=None,
            action=None,  # No validated action
            status="active",
        )
        engine = SafetyResolutionEngine(rule_context=[rule_without_action])

        finding = Finding(
            rule_id="RULE-NO-ACTION",
            severity="undetermined",
            title="Metformin + Cimetidine Interaction",
            description="Cimetidine reduces metformin clearance.",
            action=None,
            inputs={"drug_a": "Metformin", "drug_b": "Cimetidine"},
            trace={"evidence_id": "DDI-999", "source": "DDInter"},
            source="DDInter",
        )

        result = engine.resolve_finding(finding)

        self.assertEqual(result.status, "requires_human_review")
        self.assertEqual(len(result.candidates), 0)
        self.assertIsNotNone(result.review_reason)
        self.assertIn("Mandatory clinical review", result.review_reason)

    def test_missing_evidence_requires_human_review(self):
        """Test that completely unmapped finding returns requires_human_review without guessing."""
        finding = Finding(
            rule_id="UNMAPPED-RULE-777",
            severity="undetermined",
            title="Unmapped Finding",
            description="Some reported interaction without explicit guidance.",
            action=None,
            inputs={"drug_a": "DrugX", "drug_b": "DrugY"},
            trace={"evidence_id": "UNMAPPED-777", "source": "Unknown"},
            source="Unknown",
        )

        result = self.engine.resolve_finding(finding)

        self.assertEqual(result.status, "requires_human_review")
        self.assertEqual(len(result.candidates), 0)
        self.assertIn("Mandatory clinical review", result.review_reason)

    def test_mandatory_cosign_enforcement(self):
        """Test that requires_cosign is always True and cannot be set to False."""
        candidate = ResolutionCandidate(
            finding_id="F-101",
            action_type="monitor",
            description="Check potassium levels.",
            rationale="Evidence-based guidance",
            source="DDInter",
            evidence_id="E-101",
            requires_cosign=True,
        )
        self.assertTrue(candidate.requires_cosign)

        # Attempting to create candidate with requires_cosign=False must raise validation error
        with self.assertRaises(ValidationError):
            ResolutionCandidate(
                finding_id="F-101",
                action_type="monitor",
                description="Check potassium levels.",
                rationale="Evidence-based guidance",
                source="DDInter",
                evidence_id="E-101",
                requires_cosign=False,
            )

    def test_batch_resolution_processing(self):
        """Test resolve_findings processes multiple findings cleanly."""
        finding1 = Finding(
            rule_id="RULE-DDI-001",
            severity="Major",
            title="Interaction 1",
            description="Hyperkalemia risk",
            action=None,
            source="DDInter",
        )
        finding2 = Finding(
            rule_id="RULE-UNKNOWN",
            severity="undetermined",
            title="Interaction 2",
            description="Unknown risk",
            action=None,
            source="DDInter",
        )

        results = self.engine.resolve_findings([finding1, finding2])

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].status, "actionable")
        self.assertEqual(len(results[0].candidates), 1)
        self.assertEqual(results[1].status, "requires_human_review")
        self.assertEqual(len(results[1].candidates), 0)

    def test_orm_finding_compatibility(self):
        """Test resolution engine compatibility with SQLAlchemy ORM Finding model."""
        orm_finding = ORMFinding(
            id=55,
            patient_id=200,
            rule_id="RULE-DDI-001",
            severity="Major",
            title="Lisinopril + Spironolactone",
            description="Hyperkalemia risk",
            action=None,
            trace={"source": "DDInter", "evidence_id": "DDI-EVID-101"},
        )

        result = self.engine.resolve_finding(orm_finding)
        self.assertEqual(result.status, "actionable")
        self.assertEqual(result.finding_id, 55)
        self.assertEqual(len(result.candidates), 1)


if __name__ == "__main__":
    unittest.main()
