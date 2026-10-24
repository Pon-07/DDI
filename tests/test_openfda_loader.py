import tempfile
import unittest
from pathlib import Path

import pandas as pd

from agents.knowledge.openfda_loader import OpenFDALoader
from agents.knowledge.schemas import DrugKnowledge, LabelEvidence


class TestOpenFDALoader(unittest.TestCase):
    """Unit tests for the OpenFDALoader using tiny synthetic CSV fixtures."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.loader = OpenFDALoader(source_name="openFDA", source_version="2026.1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_valid_synthetic_openfda_csv(self):
        csv_file = self.dir_path / "synthetic_openfda.csv"
        df = pd.DataFrame(
            [
                {
                    "id": "FDA-001",
                    "brand_name": "Glucophage",
                    "generic_name": "Metformin Hydrochloride",
                    "dosage_and_administration": "Initial dose: 500 mg orally twice daily with meals.",
                    "contraindications": "Severe renal impairment (eGFR below 30 mL/min/1.73 m2).",
                    "drug_interactions": "Carbonic anhydrase inhibitors may increase risk of lactic acidosis.",
                    "warnings_and_cautions": "Lactic acidosis is a rare, but serious complication.",
                    "use_in_specific_populations": "Safety and effectiveness in pediatric patients below 10 years not established.",
                    "renal_related": "Assess renal function prior to initiation of therapy.",
                    "hepatic_related": "Avoid use in patients with hepatic impairment.",
                },
                {
                    "id": "FDA-002",
                    "brand_name": "Lipitor",
                    "generic_name": "Atorvastatin Calcium",
                    "dosage_and_administration": "10 to 80 mg once daily.",
                    "contraindications": "Active liver disease.",
                    "drug_interactions": "Concomitant use with strong CYP3A4 inhibitors increases plasma concentration.",
                    "warnings_and_cautions": "Myopathy and rhabdomyolysis have been reported.",
                    "use_in_specific_populations": "",
                    "renal_related": None,
                    "hepatic_related": "Active liver disease or unexplained persistent elevations in transaminases.",
                },
            ]
        )
        df.to_csv(csv_file, index=False)

        drugs, evidence = self.loader.load_csv(csv_file)

        # Verify DrugKnowledge extraction
        self.assertGreater(len(drugs), 0)
        drug_names = [d.drug_name for d in drugs]
        normalized_names = [d.normalized_name for d in drugs]

        self.assertIn("Metformin Hydrochloride", drug_names)
        self.assertIn("metformin", normalized_names)
        self.assertIn("Glucophage", drug_names)
        self.assertIn("Atorvastatin Calcium", drug_names)
        self.assertIn("atorvastatin", normalized_names)

        # Verify provenance
        for d in drugs:
            self.assertEqual(d.source, "openFDA")
            self.assertEqual(d.source_version, "2026.1")
            self.assertIn(d.label_id, ["FDA-001", "FDA-002"])

        # Verify LabelEvidence extraction
        self.assertGreater(len(evidence), 0)
        sections = {e.section for e in evidence}
        self.assertIn("contraindications", sections)
        self.assertIn("drug_interactions", sections)
        self.assertIn("warnings_and_cautions", sections)
        self.assertIn("renal_related", sections)
        self.assertIn("hepatic_related", sections)

        for e in evidence:
            self.assertEqual(e.source, "openFDA")
            self.assertEqual(e.source_version, "2026.1")
            self.assertIn(e.evidence_id, ["FDA-001", "FDA-002"])
            self.assertTrue(len(e.text.strip()) > 0)

        # Verify empty sections are skipped (e.g., FDA-002 renal_related is None, use_in_specific_populations is "")
        fda_002_evidence = [e for e in evidence if e.evidence_id == "FDA-002"]
        fda_002_sections = {e.section for e in fda_002_evidence}
        self.assertNotIn("renal_related", fda_002_sections)
        self.assertNotIn("use_in_specific_populations", fda_002_sections)

    def test_missing_required_column_raises_error(self):
        csv_file = self.dir_path / "invalid_openfda.csv"
        df = pd.DataFrame(
            [
                {
                    "id": "FDA-003",
                    "brand_name": "TestDrug",
                    "generic_name": "TestGeneric",
                    # missing remaining required columns
                }
            ]
        )
        df.to_csv(csv_file, index=False)

        with self.assertRaises(ValueError) as ctx:
            self.loader.load_csv(csv_file)
        self.assertIn("missing required columns", str(ctx.exception))

    def test_missing_file_raises_error(self):
        with self.assertRaises(FileNotFoundError):
            self.loader.load_csv(self.dir_path / "non_existent.csv")


if __name__ == "__main__":
    unittest.main()
