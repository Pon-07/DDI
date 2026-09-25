import tempfile
import unittest
from pathlib import Path

import pandas as pd

from agents.knowledge import (
    DrugKnowledge,
    DrugNormalizer,
    InteractionEvidence,
    KnowledgeService,
    LabelEvidence,
)


class TestKnowledgeService(unittest.TestCase):
    """Unit tests for the deterministic KnowledgeService."""

    def setUp(self):
        self.normalizer = DrugNormalizer(
            alias_map={
                "lipitor": "atorvastatin",
                "glucophage": "metformin",
            }
        )
        self.service = KnowledgeService(normalizer=self.normalizer)

        # Seed synthetic DrugKnowledge
        self.service.add_drugs(
            [
                DrugKnowledge(
                    drug_name="Metformin Hydrochloride",
                    normalized_name="metformin",
                    rxnorm_code="6809",
                    source="openFDA",
                    source_version="2026.1",
                    label_id="FDA-001",
                ),
                DrugKnowledge(
                    drug_name="Glucophage",
                    normalized_name="metformin",
                    rxnorm_code="6809",
                    source="openFDA",
                    source_version="2026.1",
                    label_id="FDA-001",
                ),
                DrugKnowledge(
                    drug_name="Lisinopril",
                    normalized_name="lisinopril",
                    rxnorm_code="29046",
                    source="RxNorm",
                    source_version="2026.1",
                    label_id="FDA-002",
                ),
                DrugKnowledge(
                    drug_name="Spironolactone",
                    normalized_name="spironolactone",
                    rxnorm_code="9997",
                    source="RxNorm",
                    source_version="2026.1",
                    label_id="FDA-003",
                ),
            ]
        )

        # Seed synthetic InteractionEvidence
        self.service.add_interactions(
            [
                InteractionEvidence(
                    drug_a="lisinopril",
                    drug_b="spironolactone",
                    description="Coadministration may increase serum potassium concentration.",
                    source="DDInter",
                    source_version="v2.0",
                    evidence_id="DDI-701",
                ),
                InteractionEvidence(
                    drug_a="metformin",
                    drug_b="cimetidine",
                    description="Cimetidine reduces metformin renal clearance.",
                    source="DDInter",
                    source_version="v2.0",
                    evidence_id="DDI-702",
                ),
            ]
        )

        # Seed synthetic LabelEvidence
        self.service.add_label_evidences(
            [
                LabelEvidence(
                    drug_name="Metformin Hydrochloride",
                    section="contraindications",
                    text="Severe renal impairment (eGFR < 30 mL/min/1.73 m2).",
                    source="openFDA",
                    source_version="2026.1",
                    evidence_id="FDA-001",
                ),
                LabelEvidence(
                    drug_name="Metformin Hydrochloride",
                    section="warnings_and_cautions",
                    text="Postmarketing cases of metformin-associated lactic acidosis have resulted in death.",
                    source="openFDA",
                    source_version="2026.1",
                    evidence_id="FDA-001",
                ),
                LabelEvidence(
                    drug_name="Lisinopril",
                    section="contraindications",
                    text="History of angioedema related to previous ACE inhibitor treatment.",
                    source="openFDA",
                    source_version="2026.1",
                    evidence_id="FDA-002",
                ),
            ]
        )

    def test_get_drug_exact_and_normalized(self):
        # Exact match
        drug = self.service.get_drug("Metformin Hydrochloride")
        self.assertIsNotNone(drug)
        self.assertEqual(drug.normalized_name, "metformin")
        self.assertEqual(drug.source, "openFDA")

        # Case-insensitive
        drug_ci = self.service.get_drug("metformin hydrochloride")
        self.assertIsNotNone(drug_ci)
        self.assertEqual(drug_ci.drug_name, "Metformin Hydrochloride")

        # Normalized with dosage / salt stripping
        drug_norm = self.service.get_drug("Metformin 500mg Oral Tablet")
        self.assertIsNotNone(drug_norm)
        self.assertEqual(drug_norm.normalized_name, "metformin")

        # Brand alias lookup
        drug_brand = self.service.get_drug("Glucophage")
        self.assertIsNotNone(drug_brand)

        # Non-existent drug
        self.assertIsNone(self.service.get_drug("UnknownDrugXYZ"))

    def test_search_drug(self):
        results = self.service.search_drug("met")
        names = [d.drug_name for d in results]
        self.assertIn("Metformin Hydrochloride", names)

        results_l = self.service.search_drug("lisino")
        self.assertEqual(len(results_l), 1)
        self.assertEqual(results_l[0].normalized_name, "lisinopril")

        # Non-matching search
        self.assertEqual(len(self.service.search_drug("nonexistent")), 0)

    def test_get_interactions_bidirectional(self):
        # Querying drug_a
        interactions_a = self.service.get_interactions("lisinopril")
        self.assertEqual(len(interactions_a), 1)
        self.assertEqual(interactions_a[0].evidence_id, "DDI-701")
        self.assertEqual(interactions_a[0].source, "DDInter")

        # Querying drug_b with dosage form
        interactions_b = self.service.get_interactions("Spironolactone 25mg Tablet")
        self.assertEqual(len(interactions_b), 1)
        self.assertEqual(interactions_b[0].evidence_id, "DDI-701")

        # Querying drug with other interaction
        interactions_met = self.service.get_interactions("Metformin")
        self.assertEqual(len(interactions_met), 1)
        self.assertEqual(interactions_met[0].evidence_id, "DDI-702")

        # No interactions
        self.assertEqual(len(self.service.get_interactions("Aspirin")), 0)

    def test_get_label_evidence_all_and_filtered(self):
        # All sections for Metformin
        all_met = self.service.get_label_evidence("Metformin Hydrochloride")
        self.assertEqual(len(all_met), 2)
        sections = {e.section for e in all_met}
        self.assertEqual(sections, {"contraindications", "warnings_and_cautions"})

        # Normalized query for Metformin
        norm_met = self.service.get_label_evidence("Metformin 500 mg")
        self.assertEqual(len(norm_met), 2)

        # Filtered by section
        contra_met = self.service.get_label_evidence(
            "Metformin", section="contraindications"
        )
        self.assertEqual(len(contra_met), 1)
        self.assertEqual(contra_met[0].section, "contraindications")
        self.assertIn("eGFR < 30", contra_met[0].text)
        self.assertEqual(contra_met[0].evidence_id, "FDA-001")
        self.assertEqual(contra_met[0].source, "openFDA")

        # Non-existent section
        self.assertEqual(
            len(self.service.get_label_evidence("Metformin", section="non_existent")),
            0,
        )

    def test_load_openfda_csv_integration(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = Path(tmp_dir) / "test_openfda.csv"
            df = pd.DataFrame(
                [
                    {
                        "id": "FDA-999",
                        "brand_name": "Coumadin",
                        "generic_name": "Warfarin Sodium",
                        "dosage_and_administration": "Individualized dosage.",
                        "contraindications": "Hemorrhagic tendencies.",
                        "drug_interactions": "CYP2C9 inhibitors increase anticoagulant effect.",
                        "warnings_and_cautions": "Can cause major or fatal bleeding.",
                        "use_in_specific_populations": "Pregnancy Category X.",
                        "renal_related": None,
                        "hepatic_related": "Hepatic impairment may potentiate warfarin response.",
                    }
                ]
            )
            df.to_csv(csv_path, index=False)

            fresh_service = KnowledgeService()
            fresh_service.load_openfda_csv(csv_path, source_version="2026.1")

            drug = fresh_service.get_drug("Warfarin Sodium")
            self.assertIsNotNone(drug)
            self.assertEqual(drug.label_id, "FDA-999")

            evidence = fresh_service.get_label_evidence(
                "Warfarin", section="drug_interactions"
            )
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0].evidence_id, "FDA-999")
            self.assertIn("CYP2C9", evidence[0].text)

    def test_load_ddinter_csv_integration(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = Path(tmp_dir) / "test_ddinter.csv"
            df = pd.DataFrame(
                [
                    {
                        "DDInterID_A": "DDI-100",
                        "DDInterID_B": "DDI-200",
                        "Drug_A": "Ciprofloxacin HCl",
                        "Drug_B": "Theophylline",
                        "Level": "Major",
                        "Actions": "Ciprofloxacin may increase the serum concentration of Theophylline.",
                    }
                ]
            )
            df.to_csv(csv_path, index=False)

            fresh_service = KnowledgeService()
            errors = fresh_service.load_ddinter_csv(csv_path, source_version="v2.1")
            self.assertEqual(len(errors), 0)

            interactions = fresh_service.get_interactions("Ciprofloxacin")
            self.assertEqual(len(interactions), 1)
            self.assertEqual(interactions[0].drug_a, "ciprofloxacin")
            self.assertEqual(interactions[0].drug_b, "theophylline")
            self.assertEqual(interactions[0].evidence_id, "DDI-100_DDI-200")
            self.assertEqual(interactions[0].source, "DDInter")


if __name__ == "__main__":
    unittest.main()
