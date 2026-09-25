from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class ExplanationReport(BaseModel):
    """
    Structured deterministic explanation report derived strictly from a Finding and its evidence trace.
    """

    model_config = ConfigDict(extra="forbid")

    finding_id: Union[int, str] = Field(..., description="Unique identifier of the finding or rule")
    rule_id: Optional[str] = Field(None, description="Identifier of the detection rule or evidence source item")
    title: str = Field(..., description="Title of the detected finding")
    summary: str = Field(..., description="Concise summary statement")
    medications: List[str] = Field(default_factory=list, description="List of involved medication names")
    patient_context: Optional[Dict[str, Any]] = Field(None, description="Explicit patient context present in finding inputs")
    evidence_source: str = Field(..., description="Originating knowledge source")
    source_version: Optional[str] = Field(None, description="Version string of the evidence or rule source")
    evidence_id: Optional[str] = Field(None, description="Exact evidence identifier")
    severity: str = Field(..., description="Validated severity or 'undetermined'")
    guidance: Optional[str] = Field(None, description="Explicit validated clinical action or None")
    human_action_status: str = Field(
        ...,
        description="Explicit requirement for clinical verification (e.g., 'Mandatory clinical review required')",
    )
    full_text: str = Field(..., description="Human-readable formatted explanation text")
    is_fallback: bool = Field(default=False, description="Flag indicating if deterministic fallback was used")
    is_llm_enhanced: bool = Field(default=False, description="Flag indicating if text was rephrased by local Ollama wording model")

    @property
    def is_demo_rule(self) -> bool:
        """Deterministically distinguish prototype demo rules from external validated evidence."""
        src = (self.evidence_source or "").upper()
        rid = (self.rule_id or "").upper()
        return "DEMO" in src or "DEMO" in rid or "AEGIS_HACKATHON" in src


def render_deterministic_explanation(
    finding_id: Union[int, str],
    title: str,
    description: str,
    medications: List[str],
    patient_context: Optional[Dict[str, Any]],
    source: str,
    evidence_id: Optional[str],
    severity: str,
    action: Optional[str],
    rule_id: Optional[str] = None,
) -> str:
    """
    Render a human-readable explanation using strictly information present in the finding.
    """
    meds_str = ", ".join(medications) if medications else "Unspecified medication(s)"
    ev_id_str = f" (Evidence ID: {evidence_id})" if evidence_id else ""
    rule_display = f"{rule_id} (Finding #{finding_id})" if (rule_id and str(rule_id) != str(finding_id)) else str(finding_id)
    
    patient_ctx_str = "None specified"
    if patient_context:
        ctx_parts = []
        if "patient_id" in patient_context and patient_context["patient_id"] is not None:
            ctx_parts.append(f"Patient ID: {patient_context['patient_id']}")
        if "lab_count" in patient_context:
            ctx_parts.append(f"Evaluated Labs: {patient_context['lab_count']}")
        for k, v in patient_context.items():
            if k not in {"patient_id", "lab_count", "drug_a", "drug_b"} and v is not None:
                ctx_parts.append(f"{k}: {v}")
        if ctx_parts:
            patient_ctx_str = "; ".join(ctx_parts)

    action_str = action.strip() if (action and str(action).strip() and str(action).lower() != "none") else "No validated action provided in source knowledge"
    human_status = "Mandatory clinical cosign required prior to any order modification" if action else "Mandatory human clinical review required"

    lines = [
        f"### Medication Safety Finding: {title}",
        f"- **Rule ID**: {rule_display}",
        f"- **What Was Detected**: {description}",
        f"- **Involved Medication(s)**: {meds_str}",
        f"- **Patient Context**: {patient_ctx_str}",
        f"- **Evidence Source**: {source}{ev_id_str}",
        f"- **Severity**: {severity}",
        f"- **Validated Guidance**: {action_str}",
        f"- **Required Action Status**: {human_status}",
    ]
    return "\n".join(lines)


def render_fallback_explanation(
    finding_id: Union[int, str],
    source: str,
    evidence_id: Optional[str],
    description: str,
) -> str:
    """
    Deterministic minimal fallback explanation when content validation fails or details are missing.
    """
    ev_id_str = f" (Evidence ID: {evidence_id})" if evidence_id else ""
    lines = [
        "[Deterministic Fallback Explanation]",
        f"- Finding ID: {finding_id}",
        f"- Evidence Source: {source}{ev_id_str}",
        f"- Evidence Description: {description}",
        "- Required Action Status: Mandatory clinical review and provider verification required.",
    ]
    return "\n".join(lines)
