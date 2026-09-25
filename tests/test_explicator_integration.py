import unittest
from typing import Any, Dict

from agents.explicator import (
    ExplicatorService,
    ExplanationReport,
    ExplanationValidator,
)
from agents.resolution import (
    ResolutionPipelineResult,
    SafetyResolutionEngine,
)
from agents.risk.schemas import Finding
from agents.rules.schemas import RulePack, SafetyRule


class MockOllamaClient:
    """Mock Ollama client for isolated testing without network calls."""

    def __init__(self, response_text: str = "", raise_error: bool = False):
        self.response_text = response_text
        self.raise_error = raise_error
        self.last_prompt = None

    def generate(self, model: str, prompt: str) -> Dict[str, Any]:
        self.last_prompt = prompt
        if self.raise_error:
            raise ConnectionError("Simulated Ollama connection timeout")
        return {"response": self.response_text}


class TestExplicatorIntegration(unittest.TestCase):
    """
    Step 34 — Explicator Integration Tests:
    - Pipeline integration (Finding -> Resolution -> Explication)
    - Deterministic template explanation as default and fallback
    - Optional local Ollama strictly for wording rephrasing
    - LLM input trace containment
    - Strict validation of generated text (drug names and clinical numbers)
    - Automatic fallback to deterministic template on validation failure
    - Preservation of rule_id, source, and evidence_id
    - Zero network calls
    """

    def setUp(self):
        self.rule = SafetyRule(
            rule_id="RULE-WARFARIN-001",
            rule_type="drug_interaction",
            drug_a="warfarin",
            drug_b="fluconazole",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDI-EVID-888",
            evidence_text="Fluconazole inhibits warfarin metabolism, significantly increasing INR.",
            severity="Major",
            action="Monitor INR closely; evaluate safer alternative.",
            status="active",
        )
        self.rule_pack = RulePack(
            pack_name="TestWarfarinPack",
            version="1.0.0",
            rules=[self.rule],
        )

        self.finding = Finding(
            rule_id="RULE-WARFARIN-001",
            severity="Major",
            title="Warfarin + Fluconazole Interaction",
            description="Fluconazole inhibits warfarin metabolism, significantly increasing INR.",
            action=None,
            inputs={"drug_a": "Warfarin", "drug_b": "Fluconazole", "patient_id": 101},
            trace={
                "rule_id": "RULE-WARFARIN-001",
                "evidence_id": "DDI-EVID-888",
                "source": "DDInter",
                "matched_pair": ["warfarin", "fluconazole"],
            },
            source="DDInter",
        )

    def test_pipeline_integration_with_deterministic_explicator(self):
        """Test Finding -> Resolution -> Explicator pipeline with default deterministic template."""
        explicator = ExplicatorService()
        engine = SafetyResolutionEngine(
            rule_context=self.rule_pack,
            explicator=explicator,
        )

        pipeline_result = engine.resolve_finding_pipeline(self.finding)

        self.assertIsInstance(pipeline_result, ResolutionPipelineResult)
        self.assertEqual(pipeline_result.status, "actionable")
        self.assertIsNotNone(pipeline_result.simulated_order)

        # Check attached explanation
        explanation = pipeline_result.explanation
        self.assertIsNotNone(explanation)
        self.assertIsInstance(explanation, ExplanationReport)
        self.assertEqual(explanation.finding_id, "RULE-WARFARIN-001")
        self.assertEqual(explanation.evidence_source, "DDInter")
        self.assertEqual(explanation.evidence_id, "DDI-EVID-888")
        self.assertFalse(explanation.is_fallback)
        self.assertFalse(explanation.is_llm_enhanced)

        # Verify full text contains all required provenance fields
        self.assertIn("RULE-WARFARIN-001", explanation.full_text)
        self.assertIn("DDInter", explanation.full_text)
        self.assertIn("DDI-EVID-888", explanation.full_text)
        self.assertIn("Monitor INR closely; evaluate safer alternative.", explanation.full_text)
        self.assertIn("Mandatory clinical cosign required", explanation.full_text)

    def test_ollama_wording_rephrasing_success(self):
        """Test optional local Ollama rephrases wording cleanly when validation passes."""
        valid_rephrased = (
            "Clinical Safety Alert: Co-administration of Warfarin and Fluconazole detected for Patient ID: 101.\n"
            "- Finding ID: RULE-WARFARIN-001\n"
            "- Evidence Source: DDInter (Evidence ID: DDI-EVID-888)\n"
            "- Action: Monitor INR closely; evaluate safer alternative.\n"
            "- Status: Mandatory clinical cosign required prior to order execution."
        )
        mock_ollama = MockOllamaClient(response_text=valid_rephrased)
        explicator = ExplicatorService(ollama_client=mock_ollama)

        report = explicator.explicate(self.finding)

        self.assertFalse(report.is_fallback)
        self.assertTrue(report.is_llm_enhanced)
        self.assertEqual(report.full_text, valid_rephrased)
        self.assertIn("RULE-WARFARIN-001", report.full_text)
        self.assertIn("DDInter", report.full_text)
        self.assertIn("DDI-EVID-888", report.full_text)

    def test_ollama_input_contains_strictly_only_deterministic_trace(self):
        """Verify prompt passed to Ollama strictly contains ONLY deterministic trace/evidence."""
        mock_ollama = MockOllamaClient(
            response_text=(
                "Finding ID: RULE-WARFARIN-001\nSource: DDInter\nEvidence ID: DDI-EVID-888\n"
                "Warfarin and Fluconazole interaction observed. Mandatory clinical review required."
            )
        )
        explicator = ExplicatorService(ollama_client=mock_ollama)
        explicator.explicate(self.finding)

        prompt = mock_ollama.last_prompt
        self.assertIsNotNone(prompt)
        # Provenance must be in the prompt
        self.assertIn("RULE-WARFARIN-001", prompt)
        self.assertIn("DDI-EVID-888", prompt)
        self.assertIn("DDInter", prompt)
        self.assertIn("Warfarin", prompt)
        self.assertIn("Fluconazole", prompt)
        self.assertIn("Do NOT make clinical decisions", prompt)
        self.assertIn("Do NOT invent, add, or alter any drug names, dosages", prompt)

    def test_ollama_validation_failure_on_unverified_number_falls_back_to_template(self):
        """Test that if Ollama injects an unverified number (e.g. 500mg), it falls back to deterministic template."""
        hallucinated_text = (
            "Clinical Alert: Warfarin plus Fluconazole.\n"
            "- Finding ID: RULE-WARFARIN-001\n"
            "- Source: DDInter\n"
            "- Evidence ID: DDI-EVID-888\n"
            "- Recommendation: Reduce dose by 500 mg or wait 99 days."  # 500 and 99 are NOT in trace!
        )
        mock_ollama = MockOllamaClient(response_text=hallucinated_text)
        explicator = ExplicatorService(ollama_client=mock_ollama)

        report = explicator.explicate(self.finding)

        # Validation MUST fail on unverified numbers and fall back to deterministic template
        self.assertFalse(report.is_llm_enhanced)
        self.assertNotIn("500", report.full_text)
        self.assertNotIn("99", report.full_text)
        self.assertIn("### Medication Safety Finding", report.full_text)

    def test_ollama_validation_failure_on_foreign_drug_falls_back_to_template(self):
        """Test that if Ollama hallucinates an unrelated drug (e.g. aspirin), it falls back to deterministic template."""
        foreign_drug_text = (
            "Clinical Alert: Warfarin plus Fluconazole interaction.\n"
            "- Finding ID: RULE-WARFARIN-001\n"
            "- Source: DDInter\n"
            "- Evidence ID: DDI-EVID-888\n"
            "- Consider switching patient to Aspirin instead."  # Aspirin is NOT in the finding!
        )
        mock_ollama = MockOllamaClient(response_text=foreign_drug_text)
        explicator = ExplicatorService(ollama_client=mock_ollama)

        report = explicator.explicate(self.finding)

        # Validation MUST fail on foreign drug and fall back to deterministic template
        self.assertFalse(report.is_llm_enhanced)
        self.assertNotIn("Aspirin", report.full_text)
        self.assertIn("### Medication Safety Finding", report.full_text)

    def test_ollama_missing_rule_id_or_source_falls_back_to_template(self):
        """Test that if Ollama omits rule_id or source provenance, it falls back to deterministic template."""
        missing_provenance_text = (
            "Patient is taking Warfarin and Fluconazole together which risks bleeding.\n"
            "Please monitor INR levels closely."
            # Missing RULE-WARFARIN-001 and DDInter!
        )
        mock_ollama = MockOllamaClient(response_text=missing_provenance_text)
        explicator = ExplicatorService(ollama_client=mock_ollama)

        report = explicator.explicate(self.finding)

        self.assertFalse(report.is_llm_enhanced)
        self.assertIn("### Medication Safety Finding", report.full_text)

    def test_ollama_network_or_connection_error_falls_back_seamlessly(self):
        """Test that network connection errors from Ollama are caught and fall back seamlessly."""
        mock_ollama = MockOllamaClient(raise_error=True)
        explicator = ExplicatorService(ollama_client=mock_ollama)

        report = explicator.explicate(self.finding)

        self.assertFalse(report.is_llm_enhanced)
        self.assertIn("### Medication Safety Finding", report.full_text)
        self.assertIn("RULE-WARFARIN-001", report.full_text)


if __name__ == "__main__":
    unittest.main()
