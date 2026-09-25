import unittest

from agents.explicator import (
    ExplicatorService,
    ExplanationReport,
    ExplanationValidator,
    render_deterministic_explanation,
    render_fallback_explanation,
)
from agents.risk.schemas import Finding
from models import Finding as ORMFinding


class TestExplicator(unittest.TestCase):
    """Unit tests for the deterministic Explanation / Explicator layer."""

    def setUp(self):
        self.validator = ExplanationValidator()
        self.service = ExplicatorService(validator=self.validator)

    def test_deterministic_explanation_with_full_context(self):
        """Test generating explanation from finding with complete inputs and validated action."""
        finding = Finding(
            rule_id="RULE-DDI-101",
            severity="Major",
            title="Lisinopril + Spironolactone Interaction",
            description="Coadministration may cause severe hyperkalemia.",
            action="Monitor serum potassium within 48 to 72 hours.",
            inputs={
                "drug_a": "Lisinopril 20mg",
                "drug_b": "Spironolactone 25mg",
                "patient_id": 101,
                "lab_count": 2,
            },
            trace={
                "rule_id": "RULE-DDI-101",
                "evidence_id": "DDI-EVID-999",
                "source": "DDInter",
                "source_version": "v2.1",
                "matched_pair": ["lisinopril", "spironolactone"],
            },
            source="DDInter",
        )

        report = self.service.explicate(finding)

        self.assertIsInstance(report, ExplanationReport)
        self.assertFalse(report.is_fallback)
        self.assertEqual(report.finding_id, "RULE-DDI-101")
        self.assertEqual(report.evidence_source, "DDInter")
        self.assertEqual(report.evidence_id, "DDI-EVID-999")
        self.assertEqual(report.severity, "Major")
        self.assertEqual(report.guidance, "Monitor serum potassium within 48 to 72 hours.")
        self.assertIn("Lisinopril 20mg", report.medications)
        self.assertIn("Spironolactone 25mg", report.medications)

        # Full text content verification
        text = report.full_text
        self.assertIn("Lisinopril + Spironolactone Interaction", text)
        self.assertIn("Coadministration may cause severe hyperkalemia.", text)
        self.assertIn("Patient ID: 101", text)
        self.assertIn("DDInter (Evidence ID: DDI-EVID-999)", text)
        self.assertIn("Mandatory clinical cosign required", text)

    def test_deterministic_explanation_with_minimal_finding(self):
        """Test generating explanation when finding has no action and minimal inputs."""
        finding = Finding(
            rule_id="RULE-DDI-202",
            severity="undetermined",
            title="Warfarin + Aspirin Interaction",
            description="Increased gastrointestinal bleeding risk.",
            action=None,
            inputs={"drug_a": "Warfarin", "drug_b": "Aspirin"},
            trace={"evidence_id": "DDI-EVID-500", "source": "DDInter"},
            source="DDInter",
        )

        report = self.service.explicate(finding)

        self.assertFalse(report.is_fallback)
        self.assertEqual(report.guidance, None)
        self.assertIn("Mandatory clinical review", report.human_action_status)
        self.assertIn("No validated action provided", report.full_text)

    def test_validator_detects_unverified_fabricated_numbers(self):
        """Test that validator catches numbers that were not present in finding or trace."""
        finding_data = {
            "rule_id": "RULE-1",
            "title": "Drug A + Drug B",
            "description": "Interaction risk.",
            "inputs": {"drug_a": "Drug A", "drug_b": "Drug B", "patient_id": 10},
            "trace": {"source": "openFDA"},
        }

        # Fabricated explanation injecting '500' or '99' which are not in finding
        fabricated_text = "### Finding: Drug A + Drug B\n- Reduce dose by 500 mg or wait 99 days."
        is_valid, errors = self.validator.validate_explanation(fabricated_text, finding_data)

        self.assertFalse(is_valid)
        self.assertTrue(any("500" in e for e in errors))
        self.assertTrue(any("99" in e for e in errors))

    def test_fallback_rendering_when_validation_fails(self):
        """Test that ExplicatorService invokes fallback if a custom validator rejects candidate text."""
        class StrictFailingValidator(ExplanationValidator):
            def validate_explanation(self, explanation_text: str, finding_data: dict):
                return False, ["Simulated verification error."]

        failing_service = ExplicatorService(validator=StrictFailingValidator())
        finding = Finding(
            rule_id="RULE-FAIL-01",
            severity="Moderate",
            title="Test Finding",
            description="Sample description text.",
            action=None,
            source="DDInter",
        )

        report = failing_service.explicate(finding)

        self.assertTrue(report.is_fallback)
        self.assertIn("[Deterministic Fallback Explanation]", report.full_text)
        self.assertIn("RULE-FAIL-01", report.full_text)
        self.assertIn("Mandatory clinical review and provider verification required", report.full_text)

    def test_batch_explication(self):
        """Test explicate_many handles multiple findings deterministically."""
        f1 = Finding(
            rule_id="R1",
            severity="undetermined",
            title="Finding 1",
            description="Desc 1",
            action=None,
            source="DDInter",
        )
        f2 = Finding(
            rule_id="R2",
            severity="undetermined",
            title="Finding 2",
            description="Desc 2",
            action=None,
            source="openFDA",
        )

        reports = self.service.explicate_many([f1, f2])

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0].finding_id, "R1")
        self.assertEqual(reports[1].finding_id, "R2")

    def test_orm_finding_compatibility(self):
        """Test explicator compatibility with SQLAlchemy ORM Finding model."""
        orm_finding = ORMFinding(
            id=77,
            patient_id=300,
            rule_id="ORM-RULE-77",
            severity="Major",
            title="Metformin + Contrast",
            description="Risk of lactic acidosis.",
            action=None,
            inputs={"drug_a": "Metformin", "patient_id": 300},
            trace={"evidence_id": "FDA-LBL-77", "source": "openFDA"},
        )

        report = self.service.explicate(orm_finding)
        self.assertFalse(report.is_fallback)
        self.assertEqual(report.finding_id, 77)
        self.assertIn("Risk of lactic acidosis.", report.full_text)
        self.assertIn("Patient ID: 300", report.full_text)


if __name__ == "__main__":
    unittest.main()
