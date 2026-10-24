import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from database.database import Base
from models import Drug, InteractionEvidence, LabelEvidence, SafetyRule
from scripts.ingest_openfda import (
    main,
    print_ingestion_summary,
    print_verification_summary,
    run_ingestion,
    verify_database,
)


class TestIngestOpenFDAScript(unittest.TestCase):
    """
    Unit tests for the OpenFDA ingestion CLI and script logic using synthetic test fixtures.
    Ensures that testing does NOT require the full 20,000 record dataset.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.fixture_dir = Path(self.temp_dir.name)

        # Isolated SQLite in-memory engine for test isolation
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session: Session = self.Session()

        # Create two synthetic partition files
        self.part1_path = self.fixture_dir / "openFDA_normalized_part1.json"
        self.part2_path = self.fixture_dir / "openFDA_normalized_part2.json"

        self.part1_data = [
            {
                "source": "openfda",
                "drug_a": "Metformin",
                "drug_b": None,
                "evidence": {
                    "id": "SYN-MET-01",
                    "brand_name": "Glucophage",
                    "generic_name": "Metformin Hydrochloride",
                    "dosage_and_administration": "500mg twice daily",
                    "contraindications": "eGFR < 30 mL/min",
                    "drug_interactions": "Contrast agents",
                },
            },
            {
                "source": "openfda",
                "drug_a": "Atorvastatin",
                "drug_b": None,
                "evidence": {
                    "id": "SYN-ATOR-01",
                    "brand_name": "Lipitor",
                    "generic_name": "Atorvastatin Calcium",
                    "warnings_and_cautions": "Myopathy risk",
                },
            },
        ]

        self.part2_data = [
            {
                "source": "openfda",
                "drug_a": "Lisinopril",
                "drug_b": None,
                "evidence": {
                    "id": "SYN-LIS-01",
                    "brand_name": "Zestril",
                    "generic_name": "Lisinopril",
                    "warnings_and_cautions": "Angioedema risk",
                    "use_in_specific_populations": "Category D in pregnancy",
                },
            },
            # Malformed record to verify failure metrics
            {
                "source": "openfda",
                "drug_a": "Invalid Drug Record",
                "evidence": None,
            },
        ]

        self.part1_path.write_text(json.dumps(self.part1_data), encoding="utf-8")
        self.part2_path.write_text(json.dumps(self.part2_data), encoding="utf-8")

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_run_ingestion_synthetic_partitions(self):
        """Test executing ingestion across multiple synthetic partition files."""
        result = run_ingestion(
            input_dir=self.fixture_dir,
            batch_size=2,
            file_pattern="openFDA_normalized_part*.json",
            db_session=self.session,
        )

        self.assertEqual(result.files_processed, 2)
        self.assertEqual(result.records_read, 4)
        self.assertEqual(result.records_ingested, 3)
        self.assertEqual(result.records_failed, 1)
        self.assertEqual(result.drugs_created, 3)
        self.assertEqual(result.label_evidences_created, 6)

        # Verify DB counts via verify_database helper
        counts = verify_database(db_session=self.session)
        self.assertEqual(counts["Drug"], 3)
        self.assertEqual(counts["LabelEvidence"], 6)
        self.assertEqual(counts["InteractionEvidence"], 0)
        self.assertEqual(counts["SafetyRule"], 0)

    def test_idempotent_script_execution(self):
        """Test that re-running ingestion does not duplicate entries."""
        # Initial run
        res1 = run_ingestion(
            input_dir=self.fixture_dir,
            db_session=self.session,
        )
        self.assertEqual(res1.drugs_created, 3)
        self.assertEqual(res1.label_evidences_created, 6)

        # Second run on identical files
        res2 = run_ingestion(
            input_dir=self.fixture_dir,
            db_session=self.session,
        )
        self.assertEqual(res2.drugs_created, 0)
        self.assertEqual(res2.drugs_reused, 3)
        self.assertEqual(res2.label_evidences_created, 0)
        self.assertEqual(res2.label_evidences_skipped, 6)

        counts = verify_database(db_session=self.session)
        self.assertEqual(counts["Drug"], 3)
        self.assertEqual(counts["LabelEvidence"], 6)

    def test_missing_input_directory_raises_error(self):
        """Test that a non-existent input directory raises FileNotFoundError."""
        non_existent_path = self.fixture_dir / "does_not_exist"
        with self.assertRaises(FileNotFoundError):
            run_ingestion(input_dir=non_existent_path, db_session=self.session)

    def test_summary_printers_do_not_crash(self):
        """Test that summary print utilities execute cleanly without errors."""
        res = run_ingestion(input_dir=self.fixture_dir, db_session=self.session)
        # Verify summaries print without exception
        print_ingestion_summary(res, elapsed_time=0.15)
        counts = verify_database(db_session=self.session)
        print_verification_summary(counts)

    def test_cli_main_verify_only(self):
        """Test CLI --verify-only execution flag."""
        with patch("scripts.ingest_openfda.SessionLocal", return_value=self.session):
            exit_code = main(["--verify-only"])
            self.assertEqual(exit_code, 0)

    def test_cli_main_custom_input_dir(self):
        """Test CLI execution pointing to custom directory."""
        with patch("scripts.ingest_openfda.SessionLocal", return_value=self.session):
            exit_code = main([
                "--input-dir",
                str(self.fixture_dir),
                "--pattern",
                "openFDA_normalized_part*.json",
                "--batch-size",
                "10",
                "--skip-prevalidation",
            ])
            self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
