import unittest

from engine.demo_evaluator import (
    DemoCaseEvaluationHarness,
    CaseEvaluationReport,
    NegativeCaseReport,
    FullEvaluationSummary,
)


class TestStep43DemoEvaluationHarness(unittest.TestCase):
    """
    Automated evaluation harness tests for Step 43:
    Official AEGIS Demo Cases + Negative Resilience Tests.
    """

    def setUp(self):
        self.harness = DemoCaseEvaluationHarness()

    def test_case_1_warfarin_fluconazole_inr(self):
        """Verify Case 1: Warfarin + fluconazole with rising INR context."""
        report = self.harness.evaluate_case_1()
        self.assertIsInstance(report, CaseEvaluationReport)
        self.assertTrue(report.passed, f"Case 1 failed assertions: {[a for a in report.assertions if not a.passed]}")
        self.assertTrue(report.detection_result)
        self.assertEqual(report.matched_rule_id, "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertEqual(report.provenance.get("source"), "AEGIS_HACKATHON_DEMO")
        self.assertEqual(report.provenance.get("evidence_id"), "AEGIS-DEMO-EV-001")
        self.assertEqual(report.resolution_status, "actionable")
        self.assertGreater(report.candidate_count, 0)
        self.assertEqual(report.simulated_order_status, "pending_cosign")
        self.assertEqual(report.explanation_status, "generated")
        self.assertEqual(report.audit_chain_status, "valid")

    def test_case_2_enoxaparin_declining_renal(self):
        """Verify Case 2: Enoxaparin with declining renal function context."""
        report = self.harness.evaluate_case_2()
        self.assertIsInstance(report, CaseEvaluationReport)
        self.assertTrue(report.passed, f"Case 2 failed assertions: {[a for a in report.assertions if not a.passed]}")
        self.assertTrue(report.detection_result)
        self.assertEqual(report.matched_rule_id, "AEGIS-DEMO-002-ENOXAPARIN-RENAL")
        self.assertEqual(report.provenance.get("source"), "AEGIS_HACKATHON_DEMO")
        self.assertEqual(report.provenance.get("evidence_id"), "AEGIS-DEMO-EV-002")
        self.assertEqual(report.resolution_status, "actionable")
        self.assertGreater(report.candidate_count, 0)
        self.assertEqual(report.simulated_order_status, "pending_cosign")
        self.assertEqual(report.explanation_status, "generated")
        self.assertEqual(report.audit_chain_status, "valid")

    def test_case_3_duplicate_ace_inhibitor(self):
        """Verify Case 3: Duplicate ACE-inhibitor therapy."""
        report = self.harness.evaluate_case_3()
        self.assertIsInstance(report, CaseEvaluationReport)
        self.assertTrue(report.passed, f"Case 3 failed assertions: {[a for a in report.assertions if not a.passed]}")
        self.assertTrue(report.detection_result)
        self.assertEqual(report.matched_rule_id, "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR")
        self.assertEqual(report.provenance.get("source"), "AEGIS_HACKATHON_DEMO")
        self.assertEqual(report.provenance.get("evidence_id"), "AEGIS-DEMO-EV-003")
        self.assertEqual(report.resolution_status, "actionable")
        self.assertGreater(report.candidate_count, 0)
        self.assertEqual(report.simulated_order_status, "pending_cosign")
        self.assertEqual(report.explanation_status, "generated")
        self.assertEqual(report.audit_chain_status, "valid")

    def test_negative_cases_resilience(self):
        """Verify the 4 negative/failure resilience cases."""
        negatives = self.harness.evaluate_negative_cases()
        self.assertEqual(len(negatives), 4)

        for case_id, report in negatives.items():
            self.assertIsInstance(report, NegativeCaseReport)
            self.assertTrue(report.passed, f"Negative case {case_id} failed: {report.details}")

    def test_full_evaluation_harness_summary(self):
        """Verify complete evaluation harness execution and summary output."""
        summary = self.harness.run_full_evaluation()
        self.assertIsInstance(summary, FullEvaluationSummary)
        self.assertTrue(summary.all_passed)
        self.assertEqual(summary.failed_cases, 0)
        self.assertEqual(summary.total_cases, 7)  # 3 demo cases + 4 negative cases
        self.assertEqual(summary.passed_cases, 7)

        # Verify formatted report generation
        text_report = self.harness.format_summary_report(summary)
        self.assertIn("AEGIS Rx CLINICAL SAFETY EVALUATION HARNESS REPORT", text_report)
        self.assertIn("CASE_1: Warfarin + Fluconazole with Rising INR", text_report)
        self.assertIn("CASE_2: Enoxaparin with Declining Renal-Function Context", text_report)
        self.assertIn("CASE_3: Duplicate ACE-Inhibitor Therapy", text_report)
        self.assertIn("NEGATIVE & FAILURE RESILIENCE CASES:", text_report)
        self.assertIn("Overall Status: PASSED", text_report)

    def test_deterministic_repeated_harness_execution(self):
        """Verify that running the harness repeatedly produces identical deterministic outcomes."""
        summary1 = self.harness.run_full_evaluation()
        summary2 = self.harness.run_full_evaluation()

        self.assertEqual(summary1.all_passed, summary2.all_passed)
        self.assertEqual(summary1.total_cases, summary2.total_cases)
        for cid in summary1.cases:
            self.assertEqual(summary1.cases[cid].matched_rule_id, summary2.cases[cid].matched_rule_id)
            self.assertEqual(summary1.cases[cid].provenance, summary2.cases[cid].provenance)
            self.assertEqual(summary1.cases[cid].candidate_count, summary2.cases[cid].candidate_count)


if __name__ == "__main__":
    unittest.main()
