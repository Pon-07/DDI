import unittest

from agents.knowledge import (
    DrugNormalizer,
    InteractionEvidence,
    KnowledgeService,
)
from agents.risk import Finding, RiskAssessmentReport, RiskDetector
from models import Lab, Medication, Patient


class TestRiskDetector(unittest.TestCase):
    """Unit tests for the RiskDetector agent."""

    def setUp(self):
        self.normalizer = DrugNormalizer(
            alias_map={
                "lipitor": "atorvastatin",
                "glucophage": "metformin",
            }
        )
        self.knowledge_service = KnowledgeService(normalizer=self.normalizer)

        # Seed known interaction
        self.knowledge_service.add_interactions(
            [
                InteractionEvidence(
                    drug_a="lisinopril",
                    drug_b="spironolactone",
                    description="Co-administration of ACE inhibitors and potassium-sparing diuretics may cause severe hyperkalemia.",
                    source="DDInter",
                    source_version="v2.1",
                    evidence_id="DDI-9001",
                ),
                InteractionEvidence(
                    drug_a="warfarin",
                    drug_b="aspirin",
                    description="Increased risk of major upper gastrointestinal bleeding.",
                    source="DDInter",
                    source_version="v2.1",
                    evidence_id="DDI-9002",
                ),
            ]
        )

    def test_detect_known_interaction_pair(self):
        detector = RiskDetector(knowledge_service=self.knowledge_service)

        patient_ctx = {"id": 101, "name": "Jane Doe"}
        medications = [
            {"drug_name": "Lisinopril 20mg Tablet", "status": "active"},
            {"drug_name": "Spironolactone 25mg Tablet", "status": "active"},
        ]
        labs = [{"test_name": "Potassium", "value": "4.5"}]

        findings = detector.detect(
            patient_context=patient_ctx,
            medications=medications,
            labs=labs,
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertIsInstance(finding, Finding)
        self.assertEqual(finding.rule_id, "DDI-9001")
        self.assertEqual(finding.source, "DDInter")
        self.assertEqual(finding.severity, "undetermined")  # Never guessed
        self.assertIsNone(finding.action)  # Never guessed
        self.assertIn("severe hyperkalemia", finding.description)
        self.assertEqual(finding.inputs["patient_id"], 101)
        self.assertEqual(finding.trace["evidence_id"], "DDI-9001")
        self.assertEqual(finding.trace["matched_pair"], ["lisinopril", "spironolactone"])

    def test_detect_no_interaction_pair(self):
        detector = RiskDetector(knowledge_service=self.knowledge_service)

        patient_ctx = {"id": 102}
        medications = [
            {"drug_name": "Metformin 500mg", "status": "active"},
            {"drug_name": "Atorvastatin 20mg", "status": "active"},
        ]

        findings = detector.detect(patient_context=patient_ctx, medications=medications)
        self.assertEqual(len(findings), 0)

    def test_single_medication_returns_no_findings(self):
        detector = RiskDetector(knowledge_service=self.knowledge_service)
        findings = detector.detect(
            patient_context={"id": 103},
            medications=[{"drug_name": "Lisinopril 10mg", "status": "active"}],
        )
        self.assertEqual(len(findings), 0)

    def test_discontinued_medications_are_ignored(self):
        detector = RiskDetector(knowledge_service=self.knowledge_service)
        medications = [
            {"drug_name": "Lisinopril 10mg", "status": "active"},
            {"drug_name": "Spironolactone 25mg", "status": "discontinued"},
        ]
        findings = detector.detect(medications=medications)
        self.assertEqual(len(findings), 0)

    def test_orm_models_compatibility(self):
        detector = RiskDetector(knowledge_service=self.knowledge_service)

        patient = Patient(id=200, patient_identifier="MRN-200", name="Test Patient")
        med1 = Medication(id=1, patient_id=200, drug_name="Warfarin Sodium 5mg", status="active")
        med2 = Medication(id=2, patient_id=200, drug_name="Aspirin 81mg", status="active")

        report = detector.evaluate(
            patient_context=patient,
            medications=[med1, med2],
        )

        self.assertIsInstance(report, RiskAssessmentReport)
        self.assertEqual(report.patient_id, 200)
        self.assertEqual(report.evaluated_medications_count, 2)
        self.assertEqual(report.total_findings_count, 1)
        self.assertEqual(report.findings[0].rule_id, "DDI-9002")


if __name__ == "__main__":
    unittest.main()
