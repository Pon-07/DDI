import tempfile
import unittest
from pathlib import Path

import pandas as pd

from agents.knowledge.ddinter_loader import DDInterLoader
from agents.knowledge.normalizer import DrugNormalizer
from agents.knowledge.schemas import InteractionEvidence


class TestDDInterLoader(unittest.TestCase):
    """Unit tests for the DDInterLoader using synthetic CSV fixtures."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.loader = DDInterLoader(source_name="DDInter", source_version="v2.0")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_standard_ddinter_csv(self):
        csv_file = self.dir_path / "synthetic_ddinter_standard.csv"
        df = pd.DataFrame(
            [
                {
                    "DDInterID_A": "DDInter123",
                    "DDInterID_B": "DDInter456",
                    "Drug_A": "Lisinopril Dihydrate",
                    "Drug_B": "Spironolactone 25mg Tablet",
                    "Level": "Major",
                    "Actions": "The risk or severity of hyperkalemia can be increased when Lisinopril is combined with Spironolactone.",
                },
                {
                    "DDInterID_A": "DDInter789",
                    "DDInterID_B": "DDInter101",
                    "Drug_A": "Warfarin Sodium",
                    "Drug_B": "Aspirin 81mg",
                    "Level": "Moderate",
                    "Actions": "The risk or severity of bleeding can be increased when Warfarin is combined with Aspirin.",
                },
            ]
        )
        df.to_csv(csv_file, index=False)

        evidences, errors = self.loader.load_csv(csv_file)

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(evidences), 2)

        # Check record 1
        e1 = evidences[0]
        self.assertEqual(e1.drug_a, "lisinopril")
        self.assertEqual(e1.drug_b, "spironolactone")
        self.assertEqual(e1.source, "DDInter")
        self.assertEqual(e1.source_version, "v2.0")
        self.assertEqual(e1.evidence_id, "DDInter123_DDInter456")
        self.assertIn("hyperkalemia", e1.description)

        # Check record 2
        e2 = evidences[1]
        self.assertEqual(e2.drug_a, "warfarin")
        self.assertEqual(e2.drug_b, "aspirin")
        self.assertEqual(e2.source, "DDInter")
        self.assertEqual(e2.evidence_id, "DDInter789_DDInter101")
        self.assertIn("bleeding", e2.description)

    def test_load_alternative_column_formats(self):
        csv_file = self.dir_path / "synthetic_ddinter_alt.csv"
        df = pd.DataFrame(
            [
                {
                    "id": "INT-999",
                    "drug_1": "Metformin Hydrochloride",
                    "drug_2": "Cimetidine",
                    "description": "Cimetidine may increase the plasma concentration of Metformin.",
                }
            ]
        )
        df.to_csv(csv_file, index=False)

        evidences, errors = self.loader.load_csv(csv_file)

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(evidences), 1)

        e = evidences[0]
        self.assertEqual(e.drug_a, "metformin")
        self.assertEqual(e.drug_b, "cimetidine")
        self.assertEqual(e.evidence_id, "INT-999")
        self.assertEqual(
            e.description,
            "Cimetidine may increase the plasma concentration of Metformin.",
        )

    def test_fallback_description_with_level_only(self):
        csv_file = self.dir_path / "synthetic_ddinter_level.csv"
        df = pd.DataFrame(
            [
                {
                    "drug_a": "Methotrexate",
                    "drug_b": "Amoxicillin",
                    "severity": "Moderate",
                }
            ]
        )
        df.to_csv(csv_file, index=False)

        evidences, errors = self.loader.load_csv(csv_file)

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(evidences), 1)
        self.assertEqual(evidences[0].drug_a, "methotrexate")
        self.assertEqual(evidences[0].drug_b, "amoxicillin")
        self.assertIn("risk level: Moderate", evidences[0].description)
        self.assertIsNone(evidences[0].evidence_id)

    def test_fallback_description_minimal_columns(self):
        csv_file = self.dir_path / "synthetic_ddinter_minimal.csv"
        df = pd.DataFrame(
            [
                {
                    "item_a": "Digoxin",
                    "item_b": "Amiodarone",
                }
            ]
        )
        df.to_csv(csv_file, index=False)

        evidences, errors = self.loader.load_csv(csv_file)

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(evidences), 1)
        self.assertEqual(evidences[0].drug_a, "digoxin")
        self.assertEqual(evidences[0].drug_b, "amiodarone")
        self.assertIn("Reported DDInter interaction between Digoxin and Amiodarone", evidences[0].description)

    def test_missing_required_drug_columns_raises_error(self):
        csv_file = self.dir_path / "synthetic_ddinter_missing_col.csv"
        df = pd.DataFrame(
            [
                {
                    "drug_a": "Lisinopril",
                    "some_other_column": "value",
                }
            ]
        )
        df.to_csv(csv_file, index=False)

        with self.assertRaises(ValueError) as ctx:
            self.loader.load_csv(csv_file)
        self.assertIn("missing essential drug column", str(ctx.exception))

    def test_handle_invalid_rows_and_report_errors(self):
        csv_file = self.dir_path / "synthetic_ddinter_invalid_rows.csv"
        df = pd.DataFrame(
            [
                {
                    "drug_a": "Lisinopril",
                    "drug_b": "Spironolactone",
                    "description": "Valid row 1",
                },
                {
                    "drug_a": "",
                    "drug_b": "Spironolactone",
                    "description": "Missing drug A",
                },
                {
                    "drug_a": "Warfarin",
                    "drug_b": None,
                    "description": "Missing drug B",
                },
                {
                    "drug_a": "Metformin",
                    "drug_b": "Glipizide",
                    "description": "Valid row 2",
                },
            ]
        )
        df.to_csv(csv_file, index=False)

        evidences, errors = self.loader.load_csv(csv_file)

        self.assertEqual(len(evidences), 2)
        self.assertEqual(len(errors), 2)

        self.assertEqual(evidences[0].drug_a, "lisinopril")
        self.assertEqual(evidences[1].drug_a, "metformin")

        self.assertEqual(errors[0]["row_index"], 1)
        self.assertEqual(errors[1]["row_index"], 2)

    def test_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            self.loader.load_csv(self.dir_path / "non_existent.csv")

    def test_custom_normalizer_and_alias(self):
        normalizer = DrugNormalizer(alias_map={"glucophage": "metformin"})
        custom_loader = DDInterLoader(
            normalizer=normalizer,
            source_name="DDInter-Custom",
            source_version="2026.1",
        )
        csv_file = self.dir_path / "synthetic_ddinter_custom.csv"
        df = pd.DataFrame(
            [
                {
                    "drug_a": "Glucophage 500mg",
                    "drug_b": "Cimetidine",
                    "description": "Interaction test",
                }
            ]
        )
        df.to_csv(csv_file, index=False)

        evidences, errors = custom_loader.load_csv(csv_file)
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(evidences), 1)
        self.assertEqual(evidences[0].drug_a, "metformin")
        self.assertEqual(evidences[0].source, "DDInter-Custom")
        self.assertEqual(evidences[0].source_version, "2026.1")


if __name__ == "__main__":
    unittest.main()
