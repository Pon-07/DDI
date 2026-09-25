import tempfile
import unittest
from pathlib import Path

import pandas as pd

from agents.knowledge import DrugNormalizer, KnowledgeService
from agents.risk import Finding, RiskAssessmentReport, RiskDetector
from models import Medication, Patient


class TestRiskDetectorDDInterIntegration(unittest.TestCase):
    """
    Integration tests connecting validated DDInter datasets through KnowledgeService to RiskDetector.
    Ensures deterministic detection without LLMs, guessing, or invented severity/action.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

        self.normalizer = DrugNormalizer(
            alias_map={
                "coumadin": "warfarin",
                "glucophage": "metformin",
                "prinivil": "lisinopril",
            }
        )
        self.knowledge_service = KnowledgeService(normalizer=self.normalizer)

        # Create synthetic DDInter CSV dataset
        self.ddinter_csv = self.dir_path / "synthetic_ddinter.csv"
        df = pd.DataFrame(
            [
                {
                    "DDInterID_A": "DDInter_001",
                    "DDInterID_B": "DDInter_002",
                    "Drug_A": "Lisinopril Dihydrate",
                    "Drug_B": "Spironolactone 25mg Tablet",
                    "Level": "Major",
                    "Actions": "Risk of severe hyperkalemia when Lisinopril is combined with Spironolactone.",
                },
                {
                    "DDInterID_A": "DDInter_003",
                    "DDInterID_B": "DDInter_004",
                    "Drug_A": "Warfarin Sodium",
                    "Drug_B": "Aspirin 81mg",
                    "Level": "Major",
                    "Actions": "Increased risk of major upper gastrointestinal bleeding when Warfarin is combined with Aspirin.",
                },
                {
                    "DDInterID_A": "DDInter_005",
                    "DDInterID_B": "DDInter_006",
                    "Drug_A": "Ciprofloxacin",
                    "Drug_B": "Theophylline",
                    "Level": "Moderate",
                    "Actions": "Ciprofloxacin reduces the hepatic metabolism of Theophylline.",
                },
            ]
        )
        df.to_csv(self.ddinter_csv, index=False)

        # Load synthetic DDInter data into KnowledgeService
        errors = self.knowledge_service.load_ddinter_csv(
            self.ddinter_csv, source_version="v2.1"
        )
        self.assertEqual(len(errors), 0)

        self.detector = RiskDetector(knowledge_service=self.knowledge_service)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_known_pair_returns_evidence_and_preserves_provenance(self):
        """Test that a known medication pair detects interaction evidence and preserves trace fields."""
        medications = [
            {"drug_name": "Lisinopril 10mg Oral Tablet", "status": "active"},
            {"drug_name": "Spironolactone 25mg", "status": "active"},
        ]
        patient_ctx = {"id": "PAT-101", "name": "Alice"}

        findings = self.detector.detect(
            patient_context=patient_ctx, medications=medications
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertIsInstance(finding, Finding)

        # Verify exact provenance and fields
        self.assertEqual(finding.rule_id, "DDInter_001_DDInter_002")
        self.assertEqual(finding.source, "DDInter")
        self.assertIn("severe hyperkalemia", finding.description)

        # Severity and action must NOT be invented if unvalidated
        self.assertEqual(finding.severity, "undetermined")
        self.assertIsNone(finding.action)

        # Verify trace fields (rule_id, evidence_id, source, source_version)
        self.assertEqual(finding.trace["rule_id"], "DDInter_001_DDInter_002")
        self.assertEqual(finding.trace["evidence_id"], "DDInter_001_DDInter_002")
        self.assertEqual(finding.trace["source"], "DDInter")
        self.assertEqual(finding.trace["source_version"], "v2.1")
        self.assertEqual(finding.trace["matched_pair"], ["lisinopril", "spironolactone"])

    def test_unknown_pair_returns_no_finding(self):
        """Test that medication pairs with no interaction knowledge return no findings."""
        medications = [
            {"drug_name": "Metformin 500mg", "status": "active"},
            {"drug_name": "Atorvastatin 20mg", "status": "active"},
        ]
        findings = self.detector.detect(medications=medications)
        self.assertEqual(len(findings), 0)

    def test_reversed_drug_order_returns_same_interaction(self):
        """Test that drug order in active medication list does not affect detection."""
        # Order 1: Warfarin first, then Aspirin
        meds_order_1 = [
            {"drug_name": "Coumadin 5mg", "status": "active"},
            {"drug_name": "Aspirin 81mg", "status": "active"},
        ]
        findings_1 = self.detector.detect(medications=meds_order_1)

        # Order 2: Aspirin first, then Warfarin
        meds_order_2 = [
            {"drug_name": "Aspirin 81mg", "status": "active"},
            {"drug_name": "Coumadin 5mg", "status": "active"},
        ]
        findings_2 = self.detector.detect(medications=meds_order_2)

        self.assertEqual(len(findings_1), 1)
        self.assertEqual(len(findings_2), 1)

        self.assertEqual(findings_1[0].rule_id, findings_2[0].rule_id)
        self.assertEqual(findings_1[0].description, findings_2[0].description)
        self.assertEqual(findings_1[0].trace["evidence_id"], findings_2[0].trace["evidence_id"])
        self.assertEqual(findings_1[0].trace["source"], "DDInter")

    def test_missing_evidence_does_not_guess_findings(self):
        """Test that missing evidence never results in guessed/hallucinated findings."""
        medications = [
            {"drug_name": "Acetaminophen 500mg", "status": "active"},
            {"drug_name": "Amoxicillin 250mg", "status": "active"},
            {"drug_name": "Omeprazole 20mg", "status": "active"},
        ]
        findings = self.detector.detect(medications=medications)
        self.assertEqual(len(findings), 0)

    def test_evaluate_report_structure_with_ddinter(self):
        """Test evaluate() report generation with mixed interacting and non-interacting drugs."""
        patient = Patient(id=301, patient_identifier="MRN-301", name="Bob")
        med1 = Medication(id=1, patient_id=301, drug_name="Ciprofloxacin 500mg", status="active")
        med2 = Medication(id=2, patient_id=301, drug_name="Theophylline 300mg", status="active")
        med3 = Medication(id=3, patient_id=301, drug_name="Acetaminophen 500mg", status="active")

        report = self.detector.evaluate(
            patient_context=patient,
            medications=[med1, med2, med3],
        )

        self.assertIsInstance(report, RiskAssessmentReport)
        self.assertEqual(report.patient_id, 301)
        self.assertEqual(report.evaluated_medications_count, 3)
        self.assertEqual(report.total_findings_count, 1)

        finding = report.findings[0]
        self.assertEqual(finding.rule_id, "DDInter_005_DDInter_006")
        self.assertEqual(finding.source, "DDInter")
        self.assertEqual(finding.trace["evidence_id"], "DDInter_005_DDInter_006")
        self.assertEqual(finding.trace["source_version"], "v2.1")
        self.assertIn("Theophylline", finding.description)


if __name__ == "__main__":
    unittest.main()
