import unittest

from agents.knowledge import (
    DrugKnowledge,
    DrugNormalizer,
    InteractionEvidence,
    KnowledgeService,
)


class TestKnowledgeLayerHardening(unittest.TestCase):
    """
    Focused isolated tests for Step 38 — Knowledge Layer Hardening:
    1. Drug-pair lookup
    2. Reverse-order drug-pair lookup (symmetric: A+B == B+A)
    3. Unknown drug/pair safety (no inference, no LLM)
    4. Duplicate records determinism
    5. Provenance preservation
    6. Conflicting evidence handling without silent merging
    7. Demo rule vs validated evidence separation
    """

    def setUp(self):
        self.normalizer = DrugNormalizer(
            alias_map={
                "coumadin": "warfarin",
                "diflucan": "fluconazole",
                "prinivil": "lisinopril",
            }
        )
        self.service = KnowledgeService(normalizer=self.normalizer)

    def test_1_drug_pair_lookup(self):
        """1. Deterministic lookup by normalized drug identity and drug pair."""
        ev = InteractionEvidence(
            drug_a="Warfarin",
            drug_b="Fluconazole",
            description="Fluconazole significantly increases warfarin plasma concentration and anticoagulant effect.",
            severity="Major",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDI-WAR-FLU-01",
        )
        added = self.service.add_interaction(ev)
        self.assertTrue(added)

        # Lookup by exact generic names
        results = self.service.get_interaction_pair("warfarin", "fluconazole")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].evidence_id, "DDI-WAR-FLU-01")
        self.assertEqual(results[0].severity, "Major")

        # Lookup by brand/alias names
        results_alias = self.service.get_interaction_pair("Coumadin", "Diflucan")
        self.assertEqual(len(results_alias), 1)
        self.assertEqual(results_alias[0].evidence_id, "DDI-WAR-FLU-01")

    def test_2_reverse_order_symmetric_drug_pair_lookup(self):
        """2. Symmetric DDI lookup guarantees A+B == B+A."""
        ev = InteractionEvidence(
            drug_a="Lisinopril",
            drug_b="Spironolactone",
            description="Concomitant use increases risk of severe hyperkalemia.",
            severity="Major",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDI-LIS-SPI-01",
        )
        self.service.add_interaction(ev)

        # A + B
        forward = self.service.get_interaction_pair("lisinopril", "spironolactone")
        # B + A
        reverse = self.service.get_interaction_pair("spironolactone", "lisinopril")

        self.assertEqual(len(forward), 1)
        self.assertEqual(len(reverse), 1)
        self.assertEqual(forward[0].evidence_id, reverse[0].evidence_id)
        self.assertEqual(forward[0].evidence_id, "DDI-LIS-SPI-01")

        # Works with alias reverse order
        alias_reverse = self.service.get_interaction_pair("Spironolactone 25mg", "Prinivil 10mg")
        self.assertEqual(len(alias_reverse), 1)
        self.assertEqual(alias_reverse[0].evidence_id, "DDI-LIS-SPI-01")

    def test_3_unknown_drug_pair_safety(self):
        """3. Handles unknown drugs/pairs safely with no guessing, hallucination, or inference."""
        # Known pair exists
        self.service.add_interaction(
            InteractionEvidence(
                drug_a="Metformin",
                drug_b="Cimetidine",
                description="Cimetidine increases metformin exposure.",
                severity="Moderate",
                source="DDInter",
                source_version="v2.1",
                evidence_id="DDI-MET-CIM-01",
            )
        )

        # Entirely unknown pair
        res_unknown_pair = self.service.get_interaction_pair("UnknownDrugX", "UnknownDrugY")
        self.assertEqual(res_unknown_pair, [])

        # One known, one unknown
        res_half_unknown = self.service.get_interaction_pair("Metformin", "UnknownDrugZ")
        self.assertEqual(res_half_unknown, [])

        # Unknown drug entity lookup
        self.assertIsNone(self.service.get_drug("ImaginaryCompound"))

        # Empty or invalid input safety
        self.assertEqual(self.service.get_interaction_pair("", "Metformin"), [])
        self.assertEqual(self.service.get_interaction_pair(None, "Metformin"), [])
        self.assertEqual(self.service.get_drug(""), None)
        self.assertEqual(self.service.get_drug(None), None)

    def test_4_duplicate_records_handling(self):
        """4. Handles duplicate knowledge records deterministically without silent duplication."""
        ev1 = InteractionEvidence(
            drug_a="Aspirin",
            drug_b="Ibuprofen",
            description="NSAID combination increases gastrointestinal ulceration risk.",
            severity="Major",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDI-ASP-IBU-01",
        )
        ev2_identical = InteractionEvidence(
            drug_a="aspirin",
            drug_b="ibuprofen",
            description="NSAID combination increases gastrointestinal ulceration risk.",
            severity="Major",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDI-ASP-IBU-01",
        )

        first_added = self.service.add_interaction(ev1)
        second_added = self.service.add_interaction(ev2_identical)

        self.assertTrue(first_added)
        self.assertFalse(second_added)  # Duplicate rejected deterministically

        results = self.service.get_interaction_pair("aspirin", "ibuprofen")
        self.assertEqual(len(results), 1)

    def test_5_provenance_preservation(self):
        """5. Returns provenance with every matched knowledge item: source, source_version, evidence_id."""
        ev = InteractionEvidence(
            drug_a="Clopidogrel",
            drug_b="Omeprazole",
            description="Omeprazole reduces antiplatelet effect of clopidogrel via CYP2C19 inhibition.",
            severity="Major",
            source="OpenFDA_Labels",
            source_version="2024.1",
            evidence_id="FDA-CLOP-OME-001",
        )
        self.service.add_interaction(ev)

        results = self.service.get_interaction_pair("clopidogrel", "omeprazole")
        self.assertEqual(len(results), 1)
        matched = results[0]

        # Verify all provenance attributes are intact
        self.assertEqual(matched.source, "OpenFDA_Labels")
        self.assertEqual(matched.source_version, "2024.1")
        self.assertEqual(matched.evidence_id, "FDA-CLOP-OME-001")
        self.assertEqual(matched.severity, "Major")
        self.assertIn("CYP2C19 inhibition", matched.description)

    def test_6_conflicting_evidence_handling(self):
        """6. Does not silently merge conflicting evidence; preserves all records with differing severity."""
        ev_major = InteractionEvidence(
            drug_a="Simvastatin",
            drug_b="Amiodarone",
            description="Amiodarone inhibits simvastatin metabolism, raising rhabdomyolysis risk.",
            severity="Major",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDI-SIM-AMI-01",
        )
        ev_moderate = InteractionEvidence(
            drug_a="Simvastatin",
            drug_b="Amiodarone",
            description="Concomitant amiodarone increases simvastatin exposure; limit simvastatin dose.",
            severity="Moderate",
            source="OtherValidatedSource",
            source_version="v1.0",
            evidence_id="OVS-SIM-AMI-02",
        )

        added_1 = self.service.add_interaction(ev_major)
        added_2 = self.service.add_interaction(ev_moderate)

        self.assertTrue(added_1)
        self.assertTrue(added_2)

        # Both records are preserved without blending, averaging, or overwriting
        results = self.service.get_interaction_pair("simvastatin", "amiodarone")
        self.assertEqual(len(results), 2)
        severities = {r.severity for r in results}
        self.assertEqual(severities, {"Major", "Moderate"})

        # Conflicting evidence detection
        self.assertTrue(self.service.has_conflicting_evidence("simvastatin", "amiodarone"))
        # Symmetric check
        self.assertTrue(self.service.has_conflicting_evidence("amiodarone", "simvastatin"))

    def test_7_demo_rule_vs_validated_evidence_separation(self):
        """7. Separates validated evidence from demo/hackathon rules."""
        validated_ev = InteractionEvidence(
            drug_a="Warfarin",
            drug_b="Fluconazole",
            description="Clinically validated CYP2C9 inhibition from DDInter dataset.",
            severity="Major",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDInter_VALIDATED_001",
        )
        demo_ev = InteractionEvidence(
            drug_a="Warfarin",
            drug_b="Fluconazole",
            description="AEGIS hackathon demo case: warfarin plus fluconazole qualitative rule.",
            severity="review_required",
            source="AEGIS_HACKATHON_DEMO",
            source_version="1.0.0",
            evidence_id="AEGIS-DEMO-EV-001",
        )

        self.service.add_interaction(validated_ev)
        self.service.add_interaction(demo_ev)

        # Verification of demo classifier
        self.assertFalse(self.service.is_demo_rule(validated_ev))
        self.assertTrue(self.service.is_demo_rule(demo_ev))

        # Query all
        all_ev = self.service.get_interaction_pair("warfarin", "fluconazole", include_demo=True)
        self.assertEqual(len(all_ev), 2)

        # Query only validated evidence (demo rules excluded)
        validated_only = self.service.get_validated_interactions("warfarin", "fluconazole")
        self.assertEqual(len(validated_only), 1)
        self.assertEqual(validated_only[0].evidence_id, "DDInter_VALIDATED_001")
        self.assertEqual(validated_only[0].source, "DDInter")

        # Query only demo rules
        demo_only = self.service.get_demo_interactions("warfarin", "fluconazole")
        self.assertEqual(len(demo_only), 1)
        self.assertEqual(demo_only[0].evidence_id, "AEGIS-DEMO-EV-001")
        self.assertEqual(demo_only[0].source, "AEGIS_HACKATHON_DEMO")


if __name__ == "__main__":
    unittest.main()
