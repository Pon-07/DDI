import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from agents.knowledge.dataset_validator import (
    CATEGORY_DUPLICATE_RECORD,
    CATEGORY_INVALID_SEVERITY,
    CATEGORY_MALFORMED_RECORD,
    CATEGORY_MISSING_DRUG_A,
    CATEGORY_MISSING_DRUG_B,
    CATEGORY_MISSING_DRUG_IDENTITY,
    CATEGORY_MISSING_SEVERITY,
    CATEGORY_SELF_INTERACTION,
    DatasetValidationError,
    DatasetValidator,
    print_validation_summary,
    validate_dataset,
)
from database.database import Base
from models import Drug, InteractionEvidence, LabelEvidence
from scripts.ingest_ddinter import run_ingestion as run_ddinter_ingestion
from scripts.ingest_openfda import main as openfda_main
from scripts.ingest_openfda import run_ingestion as run_openfda_ingestion


def _production_db_snapshot():
    db_path = Path("aegis_rx.db")
    if not db_path.exists():
        return None
    stat = db_path.stat()
    return (stat.st_mtime_ns, stat.st_size)


class TestDatasetValidator(unittest.TestCase):
    """Pre-ingestion dataset validation using temporary files and in-memory data."""

    def setUp(self):
        self.ddinter_validator = DatasetValidator(
            dataset_kind="ddinter",
            source_name="ddinter",
            source_version="2026.1",
        )
        self.openfda_validator = DatasetValidator(
            dataset_kind="openfda",
            source_name="openfda",
            source_version="2026.1",
        )
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session: Session = self.Session()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_valid_ddinter_style_records(self):
        records = [
            {
                "source": "ddinter",
                "drug_a": "Naltrexone",
                "drug_b": "Abacavir",
                "interaction_description": "Moderate interaction observed",
                "severity": "Moderate",
                "evidence": "DDInter1263 | DDInter1",
            },
            {
                "source": "ddinter",
                "drug_a": "Aspirin",
                "drug_b": "Warfarin",
                "interaction_description": "Increased bleeding",
                "severity": "Major",
                "evidence": "DDInter999",
            },
        ]
        result = self.ddinter_validator.validate_records(records)

        self.assertTrue(result.passed)
        self.assertEqual(result.dataset, "ddinter")
        self.assertEqual(result.source, "ddinter")
        self.assertEqual(result.total_records, 2)
        self.assertEqual(result.valid_records, 2)
        self.assertEqual(result.invalid_records, 0)
        self.assertEqual(result.error_counts, {})

    def test_missing_drug_a(self):
        records = [
            {
                "source": "ddinter",
                "drug_a": None,
                "drug_b": "Warfarin",
                "severity": "Major",
            }
        ]
        result = self.ddinter_validator.validate_records(records)

        self.assertFalse(result.passed)
        self.assertEqual(result.valid_records, 0)
        self.assertEqual(result.invalid_records, 1)
        self.assertEqual(result.error_counts.get(CATEGORY_MISSING_DRUG_A), 1)
        self.assertEqual(result.errors[0].category, CATEGORY_MISSING_DRUG_A)

    def test_missing_drug_b(self):
        records = [
            {
                "source": "ddinter",
                "drug_a": "Aspirin",
                "drug_b": "",
                "severity": "Major",
            }
        ]
        result = self.ddinter_validator.validate_records(records)

        self.assertFalse(result.passed)
        self.assertEqual(result.error_counts.get(CATEGORY_MISSING_DRUG_B), 1)

    def test_missing_severity(self):
        records = [
            {
                "source": "ddinter",
                "drug_a": "Aspirin",
                "drug_b": "Warfarin",
            }
        ]
        result = self.ddinter_validator.validate_records(records)

        self.assertFalse(result.passed)
        self.assertEqual(result.error_counts.get(CATEGORY_MISSING_SEVERITY), 1)

    def test_invalid_severity(self):
        records = [
            {
                "source": "ddinter",
                "drug_a": "Aspirin",
                "drug_b": "Warfarin",
                "severity": "Catastrophic",
            }
        ]
        result = self.ddinter_validator.validate_records(records)

        self.assertFalse(result.passed)
        self.assertEqual(result.error_counts.get(CATEGORY_INVALID_SEVERITY), 1)

    def test_self_interaction(self):
        records = [
            {
                "source": "ddinter",
                "drug_a": "Warfarin",
                "drug_b": "warfarin",
                "severity": "Major",
            }
        ]
        result = self.ddinter_validator.validate_records(records)

        self.assertFalse(result.passed)
        self.assertEqual(result.error_counts.get(CATEGORY_SELF_INTERACTION), 1)

    def test_duplicate_directed_pair(self):
        records = [
            {
                "source": "ddinter",
                "drug_a": "Simvastatin",
                "drug_b": "Amiodarone",
                "severity": "Major",
            },
            {
                "source": "ddinter",
                "drug_a": "Simvastatin",
                "drug_b": "Amiodarone",
                "severity": "Major",
            },
            {
                "source": "ddinter",
                "drug_a": "Amiodarone",
                "drug_b": "Simvastatin",
                "severity": "Major",
            },
        ]
        result = self.ddinter_validator.validate_records(records)

        self.assertFalse(result.passed)
        self.assertEqual(result.valid_records, 2)
        self.assertEqual(result.invalid_records, 1)
        self.assertEqual(result.error_counts.get(CATEGORY_DUPLICATE_RECORD), 1)

    def test_malformed_record(self):
        records = ["not-a-record", 12345, ["nested"]]
        result = self.ddinter_validator.validate_records(records)

        self.assertFalse(result.passed)
        self.assertEqual(result.total_records, 3)
        self.assertEqual(result.invalid_records, 3)
        self.assertEqual(result.error_counts.get(CATEGORY_MALFORMED_RECORD), 3)

    def test_missing_openfda_drug_identity_not_inferred_from_text(self):
        records = [
            {
                "source": "openfda",
                "drug_a": None,
                "evidence": {
                    "id": "FDA-EMPTY-01",
                    "brand_name": "",
                    "generic_name": None,
                    "contraindications": "Metformin is contraindicated in severe renal impairment.",
                    "drug_interactions": "Avoid iodinated contrast with metformin.",
                },
            }
        ]
        result = self.openfda_validator.validate_records(records)

        self.assertFalse(result.passed)
        self.assertEqual(result.invalid_records, 1)
        self.assertEqual(result.error_counts.get(CATEGORY_MISSING_DRUG_IDENTITY), 1)

    def test_valid_openfda_identity(self):
        records = [
            {
                "source": "openfda",
                "drug_a": "Metformin Hydrochloride",
                "evidence": {
                    "id": "FDA-MET-001",
                    "brand_name": "Glucophage",
                    "generic_name": "Metformin Hydrochloride",
                    "contraindications": "Severe renal impairment.",
                },
            },
            {
                "source": "openfda",
                "drug_a": None,
                "evidence": {
                    "id": "FDA-LIP-002",
                    "brand_name": "Lipitor",
                    "generic_name": "",
                    "warnings_and_cautions": "Myopathy risk.",
                },
            },
        ]
        result = self.openfda_validator.validate_records(records)

        self.assertTrue(result.passed)
        self.assertEqual(result.total_records, 2)
        self.assertEqual(result.valid_records, 2)
        self.assertEqual(result.invalid_records, 0)

    def test_validation_summary_pass_fail_behavior(self):
        passing = self.ddinter_validator.validate_records(
            [
                {
                    "source": "ddinter",
                    "drug_a": "DrugA",
                    "drug_b": "DrugB",
                    "severity": "Minor",
                }
            ]
        )
        failing = self.ddinter_validator.validate_records(
            [
                {
                    "source": "ddinter",
                    "drug_a": "DrugA",
                    "drug_b": "DrugA",
                    "severity": "Minor",
                }
            ]
        )

        self.assertTrue(passing.passed)
        self.assertFalse(failing.passed)
        self.assertEqual(passing.dataset, "ddinter")
        self.assertIsInstance(failing.error_counts, dict)
        self.assertGreaterEqual(len(failing.errors), 1)
        print_validation_summary(passing)
        print_validation_summary(failing)

    def test_json_file_and_directory_validation(self):
        file_path = self.dir_path / "ddinter_sample.json"
        records = [
            {
                "source": "ddinter",
                "drug_a": "Drug1",
                "drug_b": "Drug2",
                "severity": "Unknown",
            }
        ]
        file_path.write_text(json.dumps(records), encoding="utf-8")

        file_result = validate_dataset(
            file_path,
            dataset_kind="ddinter",
            source_name="ddinter",
            source_version="2026.1",
        )
        self.assertTrue(file_result.passed)
        self.assertEqual(file_result.files_processed, 1)
        self.assertEqual(file_result.total_records, 1)

        dir_result = self.ddinter_validator.validate_path(self.dir_path)
        self.assertTrue(dir_result.passed)

    def test_source_inconsistency(self):
        records = [
            {
                "source": "openfda",
                "drug_a": "Aspirin",
                "drug_b": "Warfarin",
                "severity": "Major",
            }
        ]
        result = self.ddinter_validator.validate_records(records)
        self.assertFalse(result.passed)
        self.assertIn("source_inconsistency", result.error_counts)

    def test_validation_does_not_modify_production_db(self):
        before = _production_db_snapshot()
        records = [
            {
                "source": "ddinter",
                "drug_a": "Aspirin",
                "drug_b": "Warfarin",
                "severity": "Major",
            }
        ]
        result = self.ddinter_validator.validate_records(records)
        self.assertTrue(result.passed)

        json_path = self.dir_path / "prevalidate.json"
        json_path.write_text(json.dumps(records), encoding="utf-8")
        file_result = validate_dataset(json_path, dataset_kind="ddinter")
        self.assertTrue(file_result.passed)

        after = _production_db_snapshot()
        self.assertEqual(before, after)

        import agents.knowledge.dataset_validator as validator_module

        self.assertFalse(hasattr(validator_module, "SessionLocal"))
        source = Path(validator_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("sqlalchemy", source.lower())
        self.assertNotIn("SessionLocal", source)
        self.assertNotIn("from database", source)
        self.assertNotIn("from models", source)

    def test_failed_validation_prevents_ddinter_ingestion(self):
        json_path = self.dir_path / "invalid_ddinter.json"
        json_path.write_text(
            json.dumps(
                [
                    {
                        "source": "ddinter",
                        "drug_a": "Aspirin",
                        "drug_b": "Aspirin",
                        "severity": "Major",
                    }
                ]
            ),
            encoding="utf-8",
        )

        with self.assertRaises(DatasetValidationError) as ctx:
            run_ddinter_ingestion(
                input_path=json_path,
                db_session=self.session,
                enforce_prevalidation=True,
            )

        self.assertFalse(ctx.exception.result.passed)
        self.assertEqual(self.session.query(Drug).count(), 0)
        self.assertEqual(self.session.query(InteractionEvidence).count(), 0)

    def test_failed_validation_prevents_openfda_ingestion(self):
        json_path = self.dir_path / "openFDA_normalized_part1.json"
        json_path.write_text(
            json.dumps(
                [
                    {
                        "source": "openfda",
                        "drug_a": None,
                        "evidence": {
                            "id": "FDA-NONE",
                            "generic_name": None,
                            "brand_name": "",
                            "contraindications": "Do not infer a drug name from this sentence.",
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )

        with self.assertRaises(DatasetValidationError):
            run_openfda_ingestion(
                input_dir=json_path,
                db_session=self.session,
                enforce_prevalidation=True,
            )

        self.assertEqual(self.session.query(Drug).count(), 0)
        self.assertEqual(self.session.query(LabelEvidence).count(), 0)

    def test_openfda_cli_validate_only_does_not_open_session(self):
        json_path = self.dir_path / "openFDA_normalized_part1.json"
        json_path.write_text(
            json.dumps(
                [
                    {
                        "source": "openfda",
                        "drug_a": "Lisinopril",
                        "evidence": {
                            "id": "FDA-LIS",
                            "generic_name": "Lisinopril",
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )

        with patch("scripts.ingest_openfda.SessionLocal") as mock_session:
            exit_code = openfda_main(
                [
                    "--input-dir",
                    str(json_path),
                    "--validate-only",
                ]
            )
            self.assertEqual(exit_code, 0)
            mock_session.assert_not_called()

    def test_openfda_cli_aborts_ingestion_on_validation_failure(self):
        json_path = self.dir_path / "openFDA_normalized_part1.json"
        json_path.write_text(
            json.dumps(
                [
                    {
                        "source": "openfda",
                        "drug_a": None,
                        "evidence": {
                            "id": "FDA-NONE",
                            "generic_name": None,
                            "brand_name": None,
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )

        with patch("scripts.ingest_openfda.SessionLocal") as mock_session:
            exit_code = openfda_main(["--input-dir", str(json_path)])
            self.assertEqual(exit_code, 1)
            mock_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
