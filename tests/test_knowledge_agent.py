import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from agents.knowledge import (
    BaseKnowledgeLoader,
    CSVKnowledgeLoader,
    DailyMedKnowledgeLoader,
    DDInterKnowledgeLoader,
    DrugKnowledge,
    DrugNormalizer,
    InteractionEvidence,
    JSONKnowledgeLoader,
    KnowledgeDataset,
    LabelEvidence,
    OpenFDAKnowledgeLoader,
    RxNormKnowledgeLoader,
)



class TestDrugNormalizer(unittest.TestCase):
    """Unit tests for the DrugNormalizer interface."""

    def setUp(self):
        self.normalizer = DrugNormalizer(
            alias_map={
                "lipitor": "atorvastatin",
                "glucophage": "metformin",
                "prinivil": "lisinopril",
                "zestril": "lisinopril",
                "lasix": "furosemide",
            }
        )

    def test_basic_normalization(self):
        self.assertEqual(self.normalizer.normalize("  Metformin  "), "metformin")
        self.assertEqual(self.normalizer.normalize("LISINOPRIL"), "lisinopril")

    def test_salt_removal(self):
        self.assertEqual(
            self.normalizer.normalize("Metformin Hydrochloride"), "metformin"
        )
        self.assertEqual(
            self.normalizer.normalize("Lisinopril Dihydrate"), "lisinopril"
        )
        self.assertEqual(
            self.normalizer.normalize("Amlodipine Besylate"), "amlodipine"
        )
        self.assertEqual(
            self.normalizer.normalize("Ciprofloxacin HCl"), "ciprofloxacin"
        )

    def test_dosage_form_and_strength_removal(self):
        self.assertEqual(
            self.normalizer.normalize("Metformin 500mg Oral Tablet"), "metformin"
        )
        self.assertEqual(
            self.normalizer.normalize("Amoxicillin 250 mg Capsule"), "amoxicillin"
        )
        self.assertEqual(
            self.normalizer.normalize("Digoxin 0.125 mg Tablet"), "digoxin"
        )

    def test_alias_mapping(self):
        self.assertEqual(self.normalizer.normalize("Lipitor"), "atorvastatin")
        self.assertEqual(self.normalizer.normalize("Lipitor 20mg Tablet"), "atorvastatin")
        self.assertEqual(self.normalizer.normalize("Glucophage 500mg"), "metformin")
        self.assertEqual(self.normalizer.normalize("Lasix"), "furosemide")

    def test_custom_aliases_and_registration(self):
        normalizer = DrugNormalizer()
        normalizer.register_alias("Coumadin", "warfarin")
        self.assertEqual(normalizer.normalize("Coumadin 5mg"), "warfarin")

    def test_empty_and_invalid_inputs(self):
        self.assertEqual(self.normalizer.normalize(""), "")
        self.assertEqual(self.normalizer.normalize("   "), "")
        self.assertEqual(self.normalizer.normalize(None), "")


class TestKnowledgeLoaders(unittest.TestCase):
    """Unit tests for the Knowledge Loaders interfaces."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_csv_knowledge_loader(self):
        csv_file = self.dir_path / "test_interactions.csv"
        csv_file.write_text(
            "drug_name,target_drug,mechanism\n"
            "Lisinopril Dihydrate,Spironolactone 25mg,Additive hyperkalemia\n"
            "Warfarin Sodium,Aspirin 81mg,Increased bleeding risk\n",
            encoding="utf-8",
        )

        loader = CSVKnowledgeLoader(
            source_name="test_csv",
            normalize_columns=["drug_name", "target_drug"],
        )
        dataset = loader.load(csv_file)

        self.assertIsInstance(dataset, KnowledgeDataset)
        self.assertEqual(dataset.source_name, "test_csv")
        self.assertEqual(len(dataset), 2)
        df = dataset.to_dataframe()
        self.assertIn("drug_name_normalized", df.columns)
        self.assertEqual(df["drug_name_normalized"].iloc[0], "lisinopril")
        self.assertEqual(df["target_drug_normalized"].iloc[0], "spironolactone")

    def test_json_knowledge_loader(self):
        json_file = self.dir_path / "test_drugs.json"
        data = [
            {"id": "1", "name": "Metformin", "category": "Antidiabetic"},
            {"id": "2", "name": "Atorvastatin", "category": "Statin"},
        ]
        json_file.write_text(json.dumps(data), encoding="utf-8")

        loader = JSONKnowledgeLoader(source_name="test_json")
        dataset = loader.load(json_file)

        self.assertEqual(len(dataset), 2)
        self.assertEqual(dataset.records[0]["name"], "Metformin")

    def test_openfda_loader(self):
        openfda_file = self.dir_path / "openfda_sample.csv"
        openfda_file.write_text(
            "brand_name,generic_name,indication\n"
            "Glucophage,Metformin Hydrochloride,Type 2 Diabetes\n"
            "Lipitor 20mg,Atorvastatin Calcium,Hyperlipidemia\n",
            encoding="utf-8",
        )

        loader = OpenFDAKnowledgeLoader()
        dataset = loader.load(openfda_file)

        df = dataset.to_dataframe()
        self.assertIn("drug_brand", df.columns)
        self.assertIn("drug_generic", df.columns)
        self.assertIn("drug_generic_normalized", df.columns)
        self.assertEqual(df["drug_generic_normalized"].iloc[0], "metformin")

    def test_ddinter_loader(self):
        ddinter_file = self.dir_path / "ddinter_sample.csv"
        ddinter_file.write_text(
            "drug_a,drug_b,level\n"
            "Methotrexate Sodium,Amoxicillin Capsule,Moderate\n",
            encoding="utf-8",
        )

        loader = DDInterKnowledgeLoader()
        dataset = loader.load(ddinter_file)

        df = dataset.to_dataframe()
        self.assertIn("drug_1", df.columns)
        self.assertIn("drug_2", df.columns)
        self.assertIn("drug_1_normalized", df.columns)
        self.assertEqual(df["drug_1_normalized"].iloc[0], "methotrexate")
        self.assertEqual(df["drug_2_normalized"].iloc[0], "amoxicillin")

    def test_rxnorm_loader(self):
        rxnorm_file = self.dir_path / "rxnorm_sample.csv"
        rxnorm_file.write_text(
            "rxcui,str\n"
            "6809,Metformin 500 MG Oral Tablet\n",
            encoding="utf-8",
        )

        loader = RxNormKnowledgeLoader()
        dataset = loader.load(rxnorm_file)

        df = dataset.to_dataframe()
        self.assertIn("rxnorm_id", df.columns)
        self.assertIn("concept_name_normalized", df.columns)
        self.assertEqual(df["concept_name_normalized"].iloc[0], "metformin")

    def test_dailymed_loader(self):
        dailymed_file = self.dir_path / "dailymed_sample.json"
        data = {
            "results": [
                {"set_id": "xyz-123", "drug_name": "Lisinopril", "boxed_warning": True}
            ]
        }
        dailymed_file.write_text(json.dumps(data), encoding="utf-8")

        loader = DailyMedKnowledgeLoader()
        dataset = loader.load(dailymed_file)

        self.assertEqual(len(dataset), 1)
        self.assertEqual(dataset.records[0]["drug_name"], "Lisinopril")


class TestKnowledgeSchemas(unittest.TestCase):
    """Unit tests for the internal normalized knowledge representations."""

    def test_drug_knowledge_valid(self):
        dk = DrugKnowledge(
            drug_name="Lisinopril Dihydrate",
            normalized_name="lisinopril",
            rxnorm_code="29046",
            source="openfda",
            source_version="2026-Q1",
            label_id="fda-lisinopril-001",
        )
        self.assertEqual(dk.drug_name, "Lisinopril Dihydrate")
        self.assertEqual(dk.normalized_name, "lisinopril")
        self.assertEqual(dk.rxnorm_code, "29046")
        self.assertEqual(dk.source, "openfda")

    def test_drug_knowledge_minimal(self):
        dk = DrugKnowledge(
            drug_name="Metformin",
            normalized_name="metformin",
            source="rxnorm",
        )
        self.assertEqual(dk.rxnorm_code, None)
        self.assertEqual(dk.source_version, None)
        self.assertEqual(dk.label_id, None)

    def test_drug_knowledge_validation_errors(self):
        from pydantic import ValidationError

        # Missing required fields
        with self.assertRaises(ValidationError):
            DrugKnowledge(drug_name="Metformin", source="rxnorm")  # Missing normalized_name

        with self.assertRaises(ValidationError):
            DrugKnowledge(drug_name="", normalized_name="metformin", source="rxnorm")  # Empty string

    def test_interaction_evidence_valid(self):
        ie = InteractionEvidence(
            drug_a="lisinopril",
            drug_b="spironolactone",
            description="Combined use may result in significant hyperkalemia.",
            source="ddinter",
            source_version="v2.1",
            evidence_id="DDI-7001",
        )
        self.assertEqual(ie.drug_a, "lisinopril")
        self.assertEqual(ie.drug_b, "spironolactone")
        self.assertEqual(ie.source, "ddinter")
        self.assertEqual(ie.evidence_id, "DDI-7001")

    def test_interaction_evidence_validation_errors(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            InteractionEvidence(
                drug_a="lisinopril",
                drug_b="spironolactone",
                # missing description
                source="ddinter",
            )

    def test_label_evidence_valid(self):
        le = LabelEvidence(
            drug_name="atorvastatin",
            section="contraindications",
            text="Active liver disease or unexplained persistent elevations in hepatic transaminases.",
            source="dailymed",
            source_version="2026.01",
            evidence_id="SPL-ATORV-01",
        )
        self.assertEqual(le.drug_name, "atorvastatin")
        self.assertEqual(le.section, "contraindications")
        self.assertEqual(le.source, "dailymed")
        self.assertEqual(le.evidence_id, "SPL-ATORV-01")

    def test_label_evidence_validation_errors(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            LabelEvidence(
                drug_name="atorvastatin",
                section="contraindications",
                # missing text and source
            )


if __name__ == "__main__":
    unittest.main()

