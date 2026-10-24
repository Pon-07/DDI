import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from agents.knowledge.ddinter_adapter import (
    DDInterIngestionAdapter,
    DDInterIngestionResult,
)
from agents.knowledge.normalizer import DrugNormalizer
from database.database import Base
from models import Drug, InteractionEvidence, LabelEvidence, SafetyRule


class TestDDInterAdapter(unittest.TestCase):
    """
    Unit tests for DDInter interaction evidence ingestion adapter:
    1. valid DDInter record ingestion
    2. Drug creation/reuse
    3. InteractionEvidence creation
    4. severity preservation including Unknown
    5. evidence/provenance preservation
    6. missing drug_a rejection
    7. missing drug_b rejection
    8. missing severity rejection
    9. duplicate/idempotent ingestion
    10. no automatic reverse-pair creation
    11. no SafetyRule creation
    12. malformed schema rejection
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session: Session = self.Session()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

        self.normalizer = DrugNormalizer()
        self.adapter = DDInterIngestionAdapter(
            db_session=self.session,
            normalizer=self.normalizer,
            source_name="ddinter",
            source_version="2026.1",
        )

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_valid_ddinter_record_ingestion(self):
        """1. Test valid DDInter record ingestion and result summary."""
        sample_record = {
            "source": "ddinter",
            "drug_a": "Naltrexone",
            "drug_b": "Abacavir",
            "interaction_description": "Moderate interaction observed",
            "severity": "Moderate",
            "evidence": "DDInter1263 | DDInter1",
        }

        res: DDInterIngestionResult = self.adapter.ingest_records([sample_record])

        self.assertEqual(res.records_read, 1)
        self.assertEqual(res.records_ingested, 1)
        self.assertEqual(res.records_failed, 0)
        self.assertEqual(res.drugs_created, 2)
        self.assertEqual(res.interaction_evidences_created, 1)

    def test_drug_creation_and_reuse(self):
        """2. Test Drug entity creation for both drugs and reuse across multiple pairs."""
        records = [
            {
                "source": "ddinter",
                "drug_a": "Abacavir",
                "drug_b": "Orlistat",
                "interaction_description": "Moderate",
                "severity": "Moderate",
                "evidence": "DDInter1 | DDInter1348",
            },
            {
                "source": "ddinter",
                "drug_a": "Abacavir",  # reused drug_a
                "drug_b": "Methotrexate",  # new drug_b
                "interaction_description": "Major",
                "severity": "Major",
                "evidence": "DDInter1 | DDInter500",
            },
        ]

        res = self.adapter.ingest_records(records)

        self.assertEqual(res.records_ingested, 2)
        # Abacavir (created), Orlistat (created), Abacavir (reused), Methotrexate (created)
        self.assertEqual(res.drugs_created, 3)
        self.assertEqual(res.drugs_reused, 1)

        # Verify Drug rows in SQLite
        drugs = self.session.query(Drug).all()
        self.assertEqual(len(drugs), 3)
        drug_names = {d.drug_name for d in drugs}
        self.assertEqual(drug_names, {"Abacavir", "Orlistat", "Methotrexate"})

    def test_interaction_evidence_creation_and_fields(self):
        """3. Test InteractionEvidence creation and exact column mappings."""
        record = {
            "source": "ddinter",
            "drug_a": "Aluminum hydroxide",
            "drug_b": "Dolutegravir",
            "interaction_description": "Major absorption decrease",
            "severity": "Major",
            "evidence": "DDInter58 | DDInter582",
        }

        self.adapter.ingest_records([record])

        ie = self.session.query(InteractionEvidence).first()
        self.assertIsNotNone(ie)
        self.assertEqual(ie.drug_a, "Aluminum hydroxide")
        self.assertEqual(ie.drug_b, "Dolutegravir")
        self.assertEqual(ie.description, "Major absorption decrease")
        self.assertEqual(ie.severity, "Major")
        self.assertEqual(ie.source, "ddinter")
        self.assertEqual(ie.source_version, "2026.1")
        self.assertEqual(ie.evidence_id, "DDInter58 | DDInter582")

    def test_severity_preservation_including_unknown(self):
        """4. Test that severity values (Major, Moderate, Minor, Unknown) are preserved verbatim without transformation."""
        records = [
            {
                "source": "ddinter",
                "drug_a": "DrugA",
                "drug_b": "DrugB",
                "interaction_description": "Major risk",
                "severity": "Major",
                "evidence": "DDI-1",
            },
            {
                "source": "ddinter",
                "drug_a": "DrugC",
                "drug_b": "DrugD",
                "interaction_description": "Moderate risk",
                "severity": "Moderate",
                "evidence": "DDI-2",
            },
            {
                "source": "ddinter",
                "drug_a": "DrugE",
                "drug_b": "DrugF",
                "interaction_description": "Minor risk",
                "severity": "Minor",
                "evidence": "DDI-3",
            },
            {
                "source": "ddinter",
                "drug_a": "DrugG",
                "drug_b": "DrugH",
                "interaction_description": "Unknown risk",
                "severity": "Unknown",
                "evidence": "DDI-4",
            },
        ]

        res = self.adapter.ingest_records(records)
        self.assertEqual(res.records_ingested, 4)

        severities = [
            ie.severity for ie in self.session.query(InteractionEvidence).order_by(InteractionEvidence.id).all()
        ]
        self.assertEqual(severities, ["Major", "Moderate", "Minor", "Unknown"])

    def test_evidence_provenance_preservation(self):
        """5. Test provenance fields (source, source_version, evidence_id)."""
        record = {
            "source": "ddinter_custom",
            "drug_a": "Aspirin",
            "drug_b": "Warfarin",
            "interaction_description": "Increased bleeding",
            "severity": "Major",
            "evidence": "DDInter999 | DDInter888",
        }

        self.adapter.ingest_records([record])

        ie = self.session.query(InteractionEvidence).filter_by(drug_a="Aspirin").first()
        self.assertEqual(ie.source, "ddinter_custom")
        self.assertEqual(ie.source_version, "2026.1")
        self.assertEqual(ie.evidence_id, "DDInter999 | DDInter888")

    def test_missing_drug_a_rejection(self):
        """6. Test rejection when drug_a is missing, None, or empty."""
        records = [
            {"source": "ddinter", "drug_a": "", "drug_b": "Warfarin", "severity": "Major"},
            {"source": "ddinter", "drug_a": None, "drug_b": "Warfarin", "severity": "Major"},
            {"source": "ddinter", "drug_b": "Warfarin", "severity": "Major"},  # absent key
        ]

        res = self.adapter.ingest_records(records)
        self.assertEqual(res.records_ingested, 0)
        self.assertEqual(res.records_failed, 3)
        self.assertEqual(len(res.validation_errors), 3)

    def test_missing_drug_b_rejection(self):
        """7. Test rejection when drug_b is missing, None, or empty."""
        records = [
            {"source": "ddinter", "drug_a": "Aspirin", "drug_b": "", "severity": "Major"},
            {"source": "ddinter", "drug_a": "Aspirin", "drug_b": None, "severity": "Major"},
            {"source": "ddinter", "drug_a": "Aspirin", "severity": "Major"},  # absent key
        ]

        res = self.adapter.ingest_records(records)
        self.assertEqual(res.records_ingested, 0)
        self.assertEqual(res.records_failed, 3)
        self.assertEqual(len(res.validation_errors), 3)

    def test_missing_severity_rejection(self):
        """8. Test rejection when severity is missing, None, or empty."""
        records = [
            {"source": "ddinter", "drug_a": "Aspirin", "drug_b": "Warfarin", "severity": ""},
            {"source": "ddinter", "drug_a": "Aspirin", "drug_b": "Warfarin", "severity": None},
            {"source": "ddinter", "drug_a": "Aspirin", "drug_b": "Warfarin"},  # absent key
        ]

        res = self.adapter.ingest_records(records)
        self.assertEqual(res.records_ingested, 0)
        self.assertEqual(res.records_failed, 3)
        self.assertEqual(len(res.validation_errors), 3)

    def test_duplicate_and_idempotent_ingestion(self):
        """9. Test that repeated ingestion of identical records is idempotent and does not create duplicates."""
        sample_file = self.dir_path / "sample_ddinter.json"
        data = [
            {
                "source": "ddinter",
                "drug_a": "Simvastatin",
                "drug_b": "Amiodarone",
                "interaction_description": "Rhabdomyolysis risk",
                "severity": "Major",
                "evidence": "DDInter10 | DDInter20",
            }
        ]
        sample_file.write_text(json.dumps(data), encoding="utf-8")

        # First ingestion
        res1 = self.adapter.ingest_file(sample_file)
        self.assertEqual(res1.drugs_created, 2)
        self.assertEqual(res1.interaction_evidences_created, 1)

        drug_count_1 = self.session.query(Drug).count()
        ie_count_1 = self.session.query(InteractionEvidence).count()
        self.assertEqual(drug_count_1, 2)
        self.assertEqual(ie_count_1, 1)

        # Second ingestion of identical file
        res2 = self.adapter.ingest_file(sample_file)
        self.assertEqual(res2.drugs_created, 0)
        self.assertEqual(res2.drugs_reused, 2)
        self.assertEqual(res2.interaction_evidences_created, 0)
        self.assertEqual(res2.interaction_evidences_skipped, 1)

        drug_count_2 = self.session.query(Drug).count()
        ie_count_2 = self.session.query(InteractionEvidence).count()
        self.assertEqual(drug_count_2, 2)
        self.assertEqual(ie_count_2, 1)

    def test_no_automatic_reverse_pair_creation(self):
        """10. Verify that ingesting (Drug A -> Drug B) strictly does NOT synthesize (Drug B -> Drug A)."""
        record = {
            "source": "ddinter",
            "drug_a": "Clopidogrel",
            "drug_b": "Omeprazole",
            "interaction_description": "Reduced antiplatelet effect",
            "severity": "Major",
            "evidence": "DDInter30 | DDInter40",
        }

        self.adapter.ingest_records([record])

        # Exactly 1 record in database
        total_ie = self.session.query(InteractionEvidence).count()
        self.assertEqual(total_ie, 1)

        # Directed forward pair exists
        fwd = (
            self.session.query(InteractionEvidence)
            .filter_by(drug_a="Clopidogrel", drug_b="Omeprazole")
            .first()
        )
        self.assertIsNotNone(fwd)

        # Reverse pair must NOT exist in database
        rev = (
            self.session.query(InteractionEvidence)
            .filter_by(drug_a="Omeprazole", drug_b="Clopidogrel")
            .first()
        )
        self.assertIsNone(rev)

    def test_no_safety_rule_creation(self):
        """11. Verify that DDInter ingestion strictly does NOT create SafetyRule records."""
        record = {
            "source": "ddinter",
            "drug_a": "Digoxin",
            "drug_b": "Verapamil",
            "interaction_description": "Increased digoxin levels",
            "severity": "Major",
            "evidence": "DDInter50 | DDInter60",
        }

        self.adapter.ingest_records([record])

        safety_rule_count = self.session.query(SafetyRule).count()
        self.assertEqual(safety_rule_count, 0)

        label_evidence_count = self.session.query(LabelEvidence).count()
        self.assertEqual(label_evidence_count, 0)

    def test_malformed_schema_rejection(self):
        """12. Test rejection of non-dict, corrupt, or invalid top-level JSON formats."""
        malformed = [
            "raw string",
            12345,
            [],
            {},
        ]

        res = self.adapter.ingest_records(malformed)
        self.assertEqual(res.records_ingested, 0)
        self.assertEqual(res.records_failed, 4)
        self.assertEqual(len(res.validation_errors), 4)

    def test_directory_ingestion_with_multiple_files(self):
        """Test directory ingestion with multiple JSON partition files."""
        part1 = self.dir_path / "part1.json"
        part2 = self.dir_path / "part2.json"

        part1.write_text(
            json.dumps([
                {
                    "source": "ddinter",
                    "drug_a": "Drug1",
                    "drug_b": "Drug2",
                    "severity": "Minor",
                }
            ]),
            encoding="utf-8",
        )
        part2.write_text(
            json.dumps([
                {
                    "source": "ddinter",
                    "drug_a": "Drug3",
                    "drug_b": "Drug4",
                    "severity": "Major",
                }
            ]),
            encoding="utf-8",
        )

        res = self.adapter.ingest_directory(self.dir_path)
        self.assertEqual(res.files_processed, 2)
        self.assertEqual(res.records_ingested, 2)
        self.assertEqual(res.drugs_created, 4)
        self.assertEqual(res.interaction_evidences_created, 2)


if __name__ == "__main__":
    unittest.main()
