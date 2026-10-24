import unittest

from agents.knowledge import (
    DrugNormalizer,
    InteractionEvidence,
    KnowledgeService,
)
from agents.risk import Finding, RiskAssessmentReport, RiskDetector
from tests.test_end_to_end_pipeline import TestEndToEndPipeline


class TestRiskDetectorHardening(unittest.TestCase):
    """
    Step 39 — Risk Detection Hardening Isolated In-Memory Tests:
    A. Deterministic knowledge lookup
    B. Symmetric drug-pair matching
    C. Unknown drug/pair safety
    D. Provenance propagation into Finding
    E. Patient-context DDI detection (evaluates active medications/orders only)
    F. Renal-risk context evaluation using existing validated rule data
    G. Duplicate therapy detection
    H. Missing-data behavior (explicit data_needed states)
    I. Finding deduplication for unchanged context
    J. Changed-context re-evaluation
    K. Verification that existing Steps 36–37 end-to-end tests remain passing
    """

    def setUp(self):
        self.normalizer = DrugNormalizer(
            alias_map={
                "coumadin": "warfarin",
                "diflucan": "fluconazole",
                "lovenox": "enoxaparin",
                "prinivil": "lisinopril",
                "vasotec": "enalapril",
            }
        )
        self.knowledge_service = KnowledgeService(normalizer=self.normalizer)

        # Seed validated DDInter interactions
        self.knowledge_service.add_interactions(
            [
                InteractionEvidence(
                    drug_a="Warfarin",
                    drug_b="Fluconazole",
                    description="Fluconazole inhibits CYP2C9 metabolism of warfarin.",
                    severity="Major",
                    source="DDInter",
                    source_version="v2.1",
                    evidence_id="DDI-WAR-FLU-01",
                ),
                InteractionEvidence(
                    drug_a="Lisinopril",
                    drug_b="Spironolactone",
                    description="Concomitant use increases risk of severe hyperkalemia.",
                    severity="Major",
                    source="DDInter",
                    source_version="v2.1",
                    evidence_id="DDI-LIS-SPI-01",
                ),
            ]
        )

        self.detector = RiskDetector(knowledge_service=self.knowledge_service)

    def test_a_deterministic_knowledge_lookup(self):
        """A. Deterministic knowledge lookup without guessing or inference."""
        meds = [
            {"drug_name": "Lisinopril 20mg", "status": "active"},
            {"drug_name": "Spironolactone 25mg", "status": "active"},
        ]
        patient_ctx = {"id": 1001, "patient_identifier": "MRN-1001"}

        findings = self.detector.detect(patient_context=patient_ctx, medications=meds)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "DDI-LIS-SPI-01")
        self.assertEqual(findings[0].source, "DDInter")
        self.assertIn("severe hyperkalemia", findings[0].description)

    def test_b_symmetric_drug_pair_matching(self):
        """B. Symmetric drug-pair matching: order of medications in prescription list yields identical findings."""
        meds_order_1 = [
            {"drug_name": "Warfarin Sodium 5mg", "status": "active"},
            {"drug_name": "Fluconazole 100mg", "status": "active"},
        ]
        meds_order_2 = [
            {"drug_name": "Fluconazole 100mg", "status": "active"},
            {"drug_name": "Warfarin Sodium 5mg", "status": "active"},
        ]

        findings_1 = self.detector.detect(medications=meds_order_1)
        findings_2 = self.detector.detect(medications=meds_order_2)

        self.assertEqual(len(findings_1), len(findings_2))
        self.assertEqual(findings_1[0].rule_id, findings_2[0].rule_id)
        self.assertEqual(findings_1[0].source, findings_2[0].source)
        self.assertEqual(
            findings_1[0].trace["matched_pair"], findings_2[0].trace["matched_pair"]
        )

    def test_c_unknown_drug_pair_safety(self):
        """C. Unknown drug/pair safety: never invents interactions or recommendations for uncatalogued drugs."""
        meds = [
            {"drug_name": "Metformin 500mg", "status": "active"},
            {"drug_name": "Atorvastatin 20mg", "status": "active"},
            {"drug_name": "TotallyUnknownDrug 10mg", "status": "active"},
        ]

        findings = self.detector.detect(medications=meds)
        self.assertEqual(findings, [])

    def test_d_provenance_propagation_into_finding(self):
        """D. Provenance propagation: rule_id, source, source_version, evidence_id, trace, and inputs."""
        meds = [
            {"drug_name": "Lisinopril 10mg", "status": "active"},
            {"drug_name": "Spironolactone 25mg", "status": "active"},
        ]
        patient_ctx = {"id": 1002}
        labs = [{"test_name": "Potassium", "value": "4.8", "measured_at": "2026-03-01T10:00:00Z"}]

        findings = self.detector.detect(
            patient_context=patient_ctx,
            medications=meds,
            labs=labs,
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]

        # Verify provenance fields
        self.assertEqual(finding.rule_id, "DDI-LIS-SPI-01")
        self.assertEqual(finding.source, "DDInter")
        self.assertEqual(finding.trace["evidence_id"], "DDI-LIS-SPI-01")
        self.assertEqual(finding.trace["source"], "DDInter")
        self.assertEqual(finding.trace["source_version"], "v2.1")
        self.assertEqual(finding.trace["matched_pair"], ["lisinopril", "spironolactone"])

        # Inputs audit
        self.assertEqual(finding.inputs["patient_id"], 1002)
        self.assertEqual(finding.inputs["drug_a"], "Lisinopril 10mg")
        self.assertEqual(finding.inputs["drug_b"], "Spironolactone 25mg")
        self.assertEqual(finding.inputs["lab_count"], 1)

    def test_e_patient_context_ddi_detection(self):
        """E. Patient-context DDI detection evaluates active medications/orders only."""
        # Drug B is discontinued -> should NOT trigger finding
        meds_discontinued = [
            {"drug_name": "Lisinopril 10mg", "status": "active"},
            {"drug_name": "Spironolactone 25mg", "status": "discontinued"},
        ]
        findings_inactive = self.detector.detect(medications=meds_discontinued)
        self.assertEqual(len(findings_inactive), 0)

        # Medication added via active order -> SHOULD trigger finding
        meds_base = [{"drug_name": "Lisinopril 10mg", "status": "active"}]
        active_orders = [{"drug_name": "Spironolactone 25mg", "status": "pending"}]
        findings_with_order = self.detector.detect(
            medications=meds_base,
            orders=active_orders,
        )
        self.assertEqual(len(findings_with_order), 1)
        self.assertEqual(findings_with_order[0].rule_id, "DDI-LIS-SPI-01")

        # Medication cancelled via order -> should NOT trigger finding
        cancelled_orders = [{"drug_name": "Spironolactone", "status": "cancelled"}]
        findings_cancelled = self.detector.detect(
            medications=[
                {"drug_name": "Lisinopril 10mg", "status": "active"},
                {"drug_name": "Spironolactone 25mg", "status": "active"},
            ],
            orders=cancelled_orders,
        )
        self.assertEqual(len(findings_cancelled), 0)

    def test_f_renal_risk_context_evaluation(self):
        """F. Renal-risk context evaluation using only existing validated rule data without invented numbers."""
        meds = [{"drug_name": "Enoxaparin Sodium 40mg", "status": "active"}]
        patient_ctx = {"id": 1003}

        # Declining renal function (rising creatinine trend)
        labs = [
            {"id": 1, "test_name": "Creatinine", "value": "1.1 mg/dL", "measured_at": "2026-03-01T08:00:00Z"},
            {"id": 2, "test_name": "Creatinine", "value": "1.8 mg/dL", "measured_at": "2026-03-02T08:00:00Z"},
            {"id": 3, "test_name": "Creatinine", "value": "2.6 mg/dL", "measured_at": "2026-03-03T08:00:00Z"},
        ]

        findings = self.detector.detect(
            patient_context=patient_ctx,
            medications=meds,
            labs=labs,
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.rule_id, "AEGIS-DEMO-002-ENOXAPARIN-RENAL")
        self.assertEqual(finding.severity, "review_required")
        self.assertEqual(finding.action, "review/hold/monitor; evaluate dose adjustment")
        self.assertEqual(finding.inputs["renal_context"], "declining")
        self.assertEqual(finding.inputs["latest_renal_lab"], "2.6 mg/dL")
        self.assertEqual(len(finding.inputs["renal_history"]), 3)
        self.assertEqual(finding.trace["evidence_id"], "AEGIS-DEMO-EV-002")

    def test_g_duplicate_therapy_detection(self):
        """G. Duplicate therapy detection for concomitant same-class medications."""
        patient_ctx = {"id": 1004}

        # Multiple active ACE inhibitors (Lisinopril + Enalapril)
        meds_ace = [
            {"drug_name": "Lisinopril 10mg", "status": "active"},
            {"drug_name": "Enalapril 5mg", "status": "active"},
        ]

        findings = self.detector.detect(patient_context=patient_ctx, medications=meds_ace)

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.rule_id, "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR")
        self.assertEqual(finding.severity, "review_required")
        self.assertEqual(finding.action, "duplicate therapy review")
        self.assertEqual(finding.inputs["duplicate_class"], "ace_inhibitor")
        self.assertEqual(finding.inputs["drug_a"], "Lisinopril 10mg")
        self.assertEqual(finding.inputs["drug_b"], "Enalapril 5mg")
        self.assertEqual(finding.trace["evidence_id"], "AEGIS-DEMO-EV-003")

    def test_h_missing_data_behavior(self):
        """H. Missing-data behavior returns explicit data-needed states without guessing."""
        patient_ctx = {"id": 1005}

        # 1. Warfarin + Fluconazole with NO INR lab on record
        meds_war_flu = [
            {"drug_name": "Warfarin Sodium 5mg", "status": "active"},
            {"drug_name": "Fluconazole 100mg", "status": "active"},
        ]
        # 1. Warfarin + Fluconazole evaluated under demo safety rules with NO INR lab on record
        clean_detector = RiskDetector(knowledge_service=KnowledgeService(normalizer=self.normalizer))
        findings_war = clean_detector.detect(
            patient_context=patient_ctx,
            medications=meds_war_flu,
            labs=[],  # Missing INR labs
        )
        self.assertEqual(len(findings_war), 1)
        war_finding = findings_war[0]
        self.assertEqual(war_finding.inputs["data_needed"], ["INR"])
        self.assertEqual(war_finding.inputs["inr_context"], "missing")
        self.assertIn("Missing recent INR lab", war_finding.description)

        # 2. Enoxaparin with NO renal panel on record
        meds_enox = [{"drug_name": "Enoxaparin 40mg", "status": "active"}]
        findings_enox = self.detector.detect(
            patient_context=patient_ctx,
            medications=meds_enox,
            labs=[],  # Missing renal panel
        )
        self.assertEqual(len(findings_enox), 1)
        enox_finding = findings_enox[0]
        self.assertEqual(enox_finding.inputs["data_needed"], ["eGFR", "creatinine"])
        self.assertEqual(enox_finding.inputs["renal_context"], "missing")
        self.assertIn("Missing baseline renal function panel", enox_finding.description)

    def test_i_finding_deduplication(self):
        """I. Finding deduplication: unchanged context does not generate duplicate findings."""
        meds = [
            {"drug_name": "Lisinopril 20mg", "status": "active"},
            {"drug_name": "Spironolactone 25mg", "status": "active"},
        ]
        patient_ctx = {"id": 1006}

        # Initial detection
        initial_findings = self.detector.detect(
            patient_context=patient_ctx,
            medications=meds,
        )
        self.assertEqual(len(initial_findings), 1)

        # Re-evaluation with exact same context and filter_unchanged=True
        duplicate_check = self.detector.detect(
            patient_context=patient_ctx,
            medications=meds,
            previous_findings=initial_findings,
            filter_unchanged=True,
        )
        # Should be filtered out because clinical context is identical
        self.assertEqual(len(duplicate_check), 0)

        # Standalone filter_unchanged_findings check
        filtered = self.detector.filter_unchanged_findings(
            current_findings=initial_findings,
            previous_findings=initial_findings,
        )
        self.assertEqual(len(filtered), 0)

    def test_j_changed_context_reevaluation(self):
        """J. Changed relevant context must trigger re-evaluation."""
        meds = [{"drug_name": "Enoxaparin 40mg", "status": "active"}]
        patient_ctx = {"id": 1007}

        # Step 1: Initial evaluation with baseline normal renal lab
        initial_labs = [
            {"id": 1, "test_name": "Creatinine", "value": "1.0 mg/dL", "measured_at": "2026-03-01T08:00:00Z"}
        ]
        initial_findings = self.detector.detect(
            patient_context=patient_ctx,
            medications=meds,
            labs=initial_labs,
        )
        self.assertEqual(len(initial_findings), 1)
        self.assertEqual(initial_findings[0].inputs["latest_renal_lab"], "1.0 mg/dL")

        # Step 2: New lab arrives indicating acute rise in creatinine
        updated_labs = [
            {"id": 1, "test_name": "Creatinine", "value": "1.0 mg/dL", "measured_at": "2026-03-01T08:00:00Z"},
            {"id": 2, "test_name": "Creatinine", "value": "2.4 mg/dL", "measured_at": "2026-03-02T08:00:00Z"},
        ]
        reevaluated = self.detector.detect(
            patient_context=patient_ctx,
            medications=meds,
            labs=updated_labs,
            previous_findings=initial_findings,
            filter_unchanged=True,
        )

        # Because clinical context changed, the finding MUST be re-evaluated and not filtered
        self.assertEqual(len(reevaluated), 1)
        self.assertEqual(reevaluated[0].inputs["latest_renal_lab"], "2.4 mg/dL")
        self.assertEqual(reevaluated[0].inputs["renal_context"], "declining")
        self.assertIn("latest Creatinine: 2.4 mg/dL", reevaluated[0].description)

    def test_k_steps_36_37_end_to_end_pipeline_remains_passing(self):
        """K. Existing Steps 36–37 end-to-end tests remain passing."""
        suite = unittest.TestLoader().loadTestsFromTestCase(TestEndToEndPipeline)
        result = unittest.TextTestRunner(verbosity=0).run(suite)
        self.assertTrue(result.wasSuccessful(), f"End-to-End Pipeline tests failed: {result.errors + result.failures}")


if __name__ == "__main__":
    unittest.main()
