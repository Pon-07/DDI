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

    def __init__(self, validator: Optional[ExplanationValidator] = None):
        self.validator = validator or ExplanationValidator()

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
        Falls back to a safe minimal representation if verification fails.
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
            or (str(finding_id) if str(finding_id) != "UNKNOWN_FINDING" else None)
        )

        patient_context = {
            k: v for k, v in inputs.items() if k not in {"drug_a", "drug_b"} and v is not None
        } if isinstance(inputs, dict) else None

        # Render candidate text
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
        )

        # Validate explanation against finding trace
        is_valid, validation_errors = self.validator.validate_explanation(
            explanation_text=candidate_text,
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
                title=title,
                summary=f"Safety finding for {', '.join(medications) if medications else title} (Fallback)",
                medications=medications,
                patient_context=patient_context,
                evidence_source=source,
                evidence_id=evidence_id,
                severity=severity,
                guidance=action,
                human_action_status=human_action_status,
                full_text=fallback_text,
                is_fallback=True,
            )

        summary_meds = f" for {', '.join(medications)}" if medications else ""
        return ExplanationReport(
            finding_id=finding_id,
            title=title,
            summary=f"Detected safety finding{summary_meds}: {description[:100]}",
            medications=medications,
            patient_context=patient_context,
            evidence_source=source,
            evidence_id=evidence_id,
            severity=severity,
            guidance=action,
            human_action_status=human_action_status,
            full_text=candidate_text,
            is_fallback=False,
        )

    def explicate_many(
        self, findings: List[Union[Finding, Dict[str, Any], Any]]
    ) -> List[ExplanationReport]:
        """Generate deterministic explanations for a collection of findings."""
        return [self.explicate(f) for f in findings]
