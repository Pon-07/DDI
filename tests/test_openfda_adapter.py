import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from agents.knowledge.normalizer import DrugNormalizer
from agents.knowledge.openfda_adapter import (
    IngestionResult,
    OpenFDAIngestionAdapter,
)
from database.database import Base
from models import Drug, InteractionEvidence, LabelEvidence, SafetyRule


class TestOpenFDAAdapter(unittest.TestCase):
    """
    Unit tests for OpenFDA evidence ingestion adapter:
    - Valid record ingestion into Drug and LabelEvidence tables
    - Rejection of malformed / missing evidence records
    - Handling of generic_name, drug_a, and brand_name fallbacks
    - Strict non-creation of InteractionEvidence and SafetyRule records
    - Provenance retention
    - Idempotent repeated ingestion
    - Directory multi-partition processing
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

        self.normalizer = DrugNormalizer(
            alias_map={
                "lipitor": "atorvastatin",
                "glucophage": "metformin",
                "prinivil": "lisinopril",
            }
        )
        self.adapter = OpenFDAIngestionAdapter(
            db_session=self.session,
            normalizer=self.normalizer,
            source_name="openfda",
            source_version="2026.1",
        )

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_valid_record_ingestion_and_provenance(self):
        """Test valid OpenFDA record ingestion, field mapping, and provenance preservation."""
        sample_record = {
            "source": "openfda",
            "drug_a": "Metformin Hydrochloride",
            "drug_b": None,
            "interaction_description": None,
            "severity": None,
            "evidence": {
                "id": "FDA-MET-001",
                "brand_name": "Glucophage",
                "generic_name": "Metformin Hydrochloride",
                "dosage_and_administration": "Initial dose: 500 mg orally twice daily with meals.",
                "contraindications": "Severe renal impairment (eGFR < 30 mL/min).",
                "drug_interactions": "Carbonic anhydrase inhibitors may increase risk of lactic acidosis.",
                "warnings_and_cautions": "Lactic acidosis is a rare, but fatal complication.",
                "use_in_specific_populations": "Safety in pediatric patients < 10 years not established.",
                "renal_related": "True",
                "hepatic_related": "True",
            },
        }

        res = self.adapter.ingest_records([sample_record])

        self.assertEqual(res.records_ingested, 1)
        self.assertEqual(res.records_failed, 0)
        self.assertEqual(res.drugs_created, 1)
        self.assertEqual(res.label_evidences_created, 5)

        # Verify Drug in SQLite
        drug = self.session.query(Drug).filter_by(normalized_name="metformin").first()
        self.assertIsNotNone(drug)
        self.assertEqual(drug.drug_name, "Metformin Hydrochloride")
        self.assertEqual(drug.source, "openfda")
        self.assertEqual(drug.source_version, "2026.1")
        self.assertEqual(drug.label_id, "FDA-MET-001")

        # Verify LabelEvidence in SQLite
        evidences = self.session.query(LabelEvidence).filter_by(drug_id=drug.id).all()
        self.assertEqual(len(evidences), 5)
        sections = {e.section for e in evidences}
        expected_sections = {
            "dosage_and_administration",
            "contraindications",
            "drug_interactions",
            "warnings_and_cautions",
            "use_in_specific_populations",
        }
        self.assertEqual(sections, expected_sections)

        for e in evidences:
            self.assertEqual(e.drug_name, "metformin")
            self.assertEqual(e.source, "openfda")
            self.assertEqual(e.source_version, "2026.1")
            self.assertEqual(e.evidence_id, "FDA-MET-001")
            self.assertTrue(len(e.text) > 0)

    def test_no_interaction_evidence_or_safety_rule_created(self):
        """Verify that OpenFDA ingestion strictly does NOT create InteractionEvidence or SafetyRule."""
        sample_record = {
            "source": "openfda",
            "drug_a": "Warfarin Sodium",
            "drug_b": None,
            "interaction_description": None,
            "severity": None,
            "evidence": {
                "id": "FDA-WARF-002",
                "brand_name": "Coumadin",
                "generic_name": "Warfarin Sodium",
                "dosage_and_administration": "Individualized dosage.",
                "contraindications": "Hemorrhagic tendencies.",
                "drug_interactions": "CYP2C9 inhibitors increase anticoagulant effect.",
                "warnings_and_cautions": "Major bleeding risk.",
                "use_in_specific_populations": "Pregnancy Category X.",
                "renal_related": "False",
                "hepatic_related": "True",
            },
        }

        self.adapter.ingest_records([sample_record])

        # InteractionEvidence must remain empty
        interaction_count = self.session.query(InteractionEvidence).count()
        self.assertEqual(interaction_count, 0)

        # SafetyRule must remain empty
        safety_rule_count = self.session.query(SafetyRule).count()
        self.assertEqual(safety_rule_count, 0)

    def test_missing_evidence_rejection(self):
        """Test rejection of malformed records lacking an evidence object."""
        malformed_records = [
            {"source": "openfda", "drug_a": "Aspirin"},  # missing evidence
            {"source": "openfda", "evidence": None},  # evidence is None
            {"source": "openfda", "evidence": "not_a_dict"},  # evidence not a dict
            "string_record",  # not a dict
        ]

        res = self.adapter.ingest_records(malformed_records)

        self.assertEqual(res.records_ingested, 0)
        self.assertEqual(res.records_failed, 4)
        self.assertEqual(len(res.validation_errors), 4)

    def test_missing_generic_name_handling_and_fallbacks(self):
        """Test drug name resolution across generic_name, drug_a, and brand_name."""
        records = [
            # 1. Has drug_a but missing generic_name in evidence
            {
                "source": "openfda",
                "drug_a": "Lisinopril Dihydrate",
                "evidence": {
                    "id": "FDA-LIS-01",
                    "brand_name": "Prinivil",
                    "generic_name": "",
                    "contraindications": "History of angioedema.",
                },
            },
            # 2. Has brand_name only
            {
                "source": "openfda",
                "drug_a": None,
                "evidence": {
                    "id": "FDA-LIP-02",
                    "brand_name": "Lipitor 20mg",
                    "generic_name": None,
                    "contraindications": "Active liver disease.",
                },
            },
            # 3. All names missing -> must be rejected
            {
                "source": "openfda",
                "drug_a": "",
                "evidence": {
                    "id": "FDA-EMPTY-03",
                    "brand_name": "",
                    "generic_name": None,
                    "contraindications": "Some text",
                },
            },
        ]

        res = self.adapter.ingest_records(records)

        self.assertEqual(res.records_ingested, 2)
        self.assertEqual(res.records_failed, 1)

        # Verify Lisinopril
        lis = self.session.query(Drug).filter_by(normalized_name="lisinopril").first()
        self.assertIsNotNone(lis)

        # Verify Lipitor / Atorvastatin
        lip = self.session.query(Drug).filter_by(normalized_name="atorvastatin").first()
        self.assertIsNotNone(lip)

    def test_duplicate_and_idempotent_ingestion(self):
        """Test that repeated ingestion does not create duplicate Drug or LabelEvidence rows."""
        sample_file = self.dir_path / "sample_openfda.json"
        data = [
            {
                "source": "openfda",
                "drug_a": "Amlodipine Besylate",
                "drug_b": None,
                "interaction_description": None,
                "severity": None,
                "evidence": {
                    "id": "FDA-AML-01",
                    "brand_name": "Norvasc",
                    "generic_name": "Amlodipine Besylate",
                    "dosage_and_administration": "5 mg to 10 mg once daily.",
                    "contraindications": "Known sensitivity to amlodipine.",
                    "drug_interactions": "Simvastatin coadministration increases simvastatin exposure.",
                    "warnings_and_cautions": "Symptomatic hypotension is possible.",
                    "use_in_specific_populations": "Elderly start at 2.5 mg.",
                },
            }
        ]
        sample_file.write_text(json.dumps(data), encoding="utf-8")

        # First ingestion
        res1 = self.adapter.ingest_file(sample_file)
        self.assertEqual(res1.drugs_created, 1)
        self.assertEqual(res1.label_evidences_created, 5)

        drug_count_1 = self.session.query(Drug).count()
        label_count_1 = self.session.query(LabelEvidence).count()
        self.assertEqual(drug_count_1, 1)
        self.assertEqual(label_count_1, 5)

        # Second ingestion of the identical file (Idempotency)
        res2 = self.adapter.ingest_file(sample_file)
        self.assertEqual(res2.drugs_created, 0)
        self.assertEqual(res2.drugs_reused, 1)
        self.assertEqual(res2.label_evidences_created, 0)
        self.assertEqual(res2.label_evidences_skipped, 5)

        drug_count_2 = self.session.query(Drug).count()
        label_count_2 = self.session.query(LabelEvidence).count()
        self.assertEqual(drug_count_2, 1)
        self.assertEqual(label_count_2, 5)

    def test_directory_ingestion_with_multiple_parts(self):
        """Test ingesting multiple partition JSON files from a directory."""
        parts_dir = self.dir_path / "normalized_parts"
        parts_dir.mkdir()

        part1 = parts_dir / "openFDA_normalized_part1.json"
        part2 = parts_dir / "openFDA_normalized_part2.json"

        part1_data = [
            {
                "source": "openfda",
                "drug_a": "Ciprofloxacin HCl",
                "evidence": {
                    "id": "FDA-CIPRO-01",
                    "generic_name": "Ciprofloxacin HCl",
                    "warnings_and_cautions": "Tendinitis and tendon rupture warning.",
                },
            }
        ]
        part2_data = [
            {
                "source": "openfda",
                "drug_a": "Theophylline",
                "evidence": {
                    "id": "FDA-THEO-02",
                    "generic_name": "Theophylline",
                    "warnings_and_cautions": "Serum theophylline concentration monitoring required.",
                },
            }
        ]

        part1.write_text(json.dumps(part1_data), encoding="utf-8")
        part2.write_text(json.dumps(part2_data), encoding="utf-8")

        res = self.adapter.ingest_directory(parts_dir)

        self.assertEqual(res.files_processed, 2)
        self.assertEqual(res.records_ingested, 2)
        self.assertEqual(res.drugs_created, 2)
        self.assertEqual(res.label_evidences_created, 2)

        drugs = self.session.query(Drug).all()
        self.assertEqual(len(drugs), 2)
        drug_names = {d.normalized_name for d in drugs}
        self.assertEqual(drug_names, {"ciprofloxacin", "theophylline"})

    def test_empty_sections_are_skipped(self):
        """Test that unpopulated or empty sections do not create blank LabelEvidence rows."""
        record = {
            "source": "openfda",
            "drug_a": "Hydrochlorothiazide",
            "evidence": {
                "id": "FDA-HCTZ-01",
                "generic_name": "Hydrochlorothiazide",
                "dosage_and_administration": "25 mg daily.",
                "contraindications": "",  # empty string
                "drug_interactions": None,  # None
                "warnings_and_cautions": "   ",  # whitespace only
                "use_in_specific_populations": "NaN",  # nan string
            },
        }

        res = self.adapter.ingest_records([record])

        self.assertEqual(res.records_ingested, 1)
        self.assertEqual(res.label_evidences_created, 1)

        evidences = self.session.query(LabelEvidence).filter_by(drug_name="hydrochlorothiazide").all()
        self.assertEqual(len(evidences), 1)
        self.assertEqual(evidences[0].section, "dosage_and_administration")


if __name__ == "__main__":
    unittest.main()
