from typing import Any, Dict, List, Optional, Union

from agents.explicator.templates import (
    ExplanationReport,
    render_deterministic_explanation,
    render_fallback_explanation,
)
from agents.explicator.validator import ExplanationValidator
from agents.risk.schemas import Finding


class ExplicatorService:
    """
    Deterministic Explanation Service for AEGIS Rx medication safety findings.
    Assembles human-readable explanations using strictly data present in the Finding
    and its evidence trace without LLM hallucinations or unverified clinical extrapolations.
    """

    def __init__(
        self,
        validator: Optional[ExplanationValidator] = None,
        ollama_client: Optional[Any] = None,
        model_name: str = "llama3:latest",
    ):
        self.validator = validator or ExplanationValidator()
        self.ollama_client = ollama_client
        self.model_name = model_name

    def _call_ollama(self, prompt: str) -> Optional[str]:
        if not self.ollama_client:
            return None
        try:
            if callable(self.ollama_client):
                res = self.ollama_client(prompt)
                if isinstance(res, dict):
                    return res.get("response") or res.get("content") or str(res)
                return str(res)
            if hasattr(self.ollama_client, "generate"):
                res = self.ollama_client.generate(model=self.model_name, prompt=prompt)
                if isinstance(res, dict):
                    return res.get("response")
                return getattr(res, "response", str(res))
            if hasattr(self.ollama_client, "chat"):
                res = self.ollama_client.chat(model=self.model_name, messages=[{"role": "user", "content": prompt}])
                if isinstance(res, dict):
                    msg = res.get("message", {})
                    return msg.get("content") if isinstance(msg, dict) else str(msg)
                return getattr(res, "content", str(res))
        except Exception:
            return None
        return None

    def _extract_data(self, finding: Any) -> Dict[str, Any]:
        """Normalize finding object or dict into a standard dictionary."""
        if isinstance(finding, dict):
            return finding
        if isinstance(finding, Finding):
            return finding.model_dump()

        # ORM model or other object
        return {
            "id": getattr(finding, "id", None),
            "rule_id": getattr(finding, "rule_id", "UNKNOWN_RULE"),
            "severity": getattr(finding, "severity", "undetermined"),
            "title": getattr(finding, "title", "Medication Safety Finding"),
            "description": getattr(finding, "description", "Reported safety finding."),
            "action": getattr(finding, "action", None),
            "inputs": getattr(finding, "inputs", {}) or {},
            "trace": getattr(finding, "trace", {}) or {},
            "source": getattr(finding, "source", "Unknown"),
        }

    def explicate(self, finding: Union[Finding, Dict[str, Any], Any]) -> ExplanationReport:
        """
        Generate a validated deterministic explanation report for a safety finding.
        Optionally uses local Ollama strictly for wording rephrasing.
        Falls back to deterministic template if Ollama is absent or validation fails.
        """
        finding_data = self._extract_data(finding)

        finding_id = (
            finding_data.get("id")
            or finding_data.get("rule_id")
            or "UNKNOWN_FINDING"
        )
        title = finding_data.get("title", "Medication Safety Finding")
        description = finding_data.get("description", "Safety finding detected.")
        severity = finding_data.get("severity", "undetermined")
        action = finding_data.get("action")
        source = finding_data.get("source") or "Unknown"

        inputs = finding_data.get("inputs", {}) or {}
        trace = finding_data.get("trace", {}) or {}

        # Extract involved medications
        medications: List[str] = []
        if isinstance(inputs, dict):
            if "drug_a" in inputs and inputs["drug_a"]:
                medications.append(str(inputs["drug_a"]))
            if "drug_b" in inputs and inputs["drug_b"]:
                medications.append(str(inputs["drug_b"]))
        if not medications and isinstance(trace, dict):
            matched_pair = trace.get("matched_pair")
            if matched_pair and isinstance(matched_pair, list):
                medications = [str(d) for d in matched_pair]

        evidence_id = (
            (trace.get("evidence_id") if isinstance(trace, dict) else None)
            or finding_data.get("evidence_id")
        )
        rule_id = (
            finding_data.get("rule_id")
            or (trace.get("rule_id") if isinstance(trace, dict) else None)
        )
        source_version = (
            finding_data.get("source_version")
            or (trace.get("source_version") if isinstance(trace, dict) else None)
        )

        patient_context = {
            k: v for k, v in inputs.items() if k not in {"drug_a", "drug_b"} and v is not None
        } if isinstance(inputs, dict) else None

        # Render deterministic template text first (MUST be the fallback)
        candidate_text = render_deterministic_explanation(
            finding_id=finding_id,
            title=title,
            description=description,
            medications=medications,
            patient_context=patient_context,
            source=source,
            evidence_id=evidence_id,
            severity=severity,
            action=action,
            rule_id=rule_id,
        )

        final_text = candidate_text
        is_llm_enhanced = False

        # Optional local Ollama wording refinement
        if self.ollama_client:
            ollama_prompt = (
                "You are an assistant rephrasing clinical medication safety findings for readability.\n"
                "CRITICAL CONSTRAINTS:\n"
                "- Do NOT make clinical decisions or change treatments.\n"
                "- Do NOT invent, add, or alter any drug names, dosages, thresholds, or numerical values.\n"
                "- You MUST preserve the finding_id, source, and evidence_id verbatim in the text.\n\n"
                f"Finding ID: {finding_id}\n"
                f"Source: {source}\n"
                f"Evidence ID: {evidence_id}\n"
                f"Title: {title}\n"
                f"Severity: {severity}\n"
                f"Description: {description}\n"
                f"Action Guidance: {action or 'None'}\n"
                f"Medications: {', '.join(medications)}\n\n"
                f"Template text:\n{candidate_text}\n"
            )
            llm_text = self._call_ollama(ollama_prompt)
            if llm_text and llm_text.strip():
                # Verify that rule_id, source, and evidence_id are preserved
                has_ids = (str(finding_id).lower() in llm_text.lower()) and (str(source).lower() in llm_text.lower())
                if evidence_id:
                    has_ids = has_ids and (str(evidence_id).lower() in llm_text.lower())

                if has_ids:
                    is_valid_llm, _ = self.validator.validate_explanation(
                        explanation_text=llm_text,
                        finding_data=finding_data,
                    )
                    if is_valid_llm:
                        final_text = llm_text.strip()
                        is_llm_enhanced = True
                    # On validation failure, falls back to deterministic candidate_text

        # Validate final explanation against finding trace
        is_valid, validation_errors = self.validator.validate_explanation(
            explanation_text=final_text,
            finding_data=finding_data,
        )

        human_action_status = (
            "Mandatory clinical cosign required prior to order execution"
            if action
            else "Mandatory clinical review and evaluation required"
        )

        if not is_valid:
            # Fallback to minimal deterministic summary
            fallback_text = render_fallback_explanation(
                finding_id=finding_id,
                source=source,
                evidence_id=evidence_id,
                description=description,
            )
            return ExplanationReport(
                finding_id=finding_id,
                rule_id=str(rule_id) if rule_id else None,
                title=title,
                summary=f"Safety finding for {', '.join(medications) if medications else title} (Fallback)",
                medications=medications,
                patient_context=patient_context,
                evidence_source=source,
                source_version=str(source_version) if source_version else None,
                evidence_id=evidence_id,
                severity=severity,
                guidance=action,
                human_action_status=human_action_status,
                full_text=fallback_text,
                is_fallback=True,
                is_llm_enhanced=False,
            )

        summary_meds = f" for {', '.join(medications)}" if medications else ""
        return ExplanationReport(
            finding_id=finding_id,
            rule_id=str(rule_id) if rule_id else None,
            title=title,
            summary=f"Detected safety finding{summary_meds}: {description[:100]}",
            medications=medications,
            patient_context=patient_context,
            evidence_source=source,
            source_version=str(source_version) if source_version else None,
            evidence_id=evidence_id,
            severity=severity,
            guidance=action,
            human_action_status=human_action_status,
            full_text=final_text,
            is_fallback=False,
            is_llm_enhanced=is_llm_enhanced,
        )

    def explicate_many(
        self, findings: List[Union[Finding, Dict[str, Any], Any]]
    ) -> List[ExplanationReport]:
        """Generate deterministic explanations for a collection of findings."""
        return [self.explicate(f) for f in findings]
