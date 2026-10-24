from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Finding(BaseModel):
    """
    Standard finding representation for detected medication safety risks.
    Encapsulates raw evidence, deterministic matches, and provenance trace without guessing.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Identifier of the detection rule or evidence source item",
    )
    severity: str = Field(
        default="undetermined",
        min_length=1,
        max_length=50,
        description="Severity level from validated source, or 'undetermined' / 'unspecified' if not provided",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Short summary title of the safety finding",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Detailed description of the interaction or contraindication from evidence",
    )
    action: Optional[str] = Field(
        None,
        description="Clinical recommendation from validated source, or None if unspecified",
    )
    inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="Input parameters, medication names, and patient context that triggered the finding",
    )
    trace: Dict[str, Any] = Field(
        default_factory=dict,
        description="Audit trace preserving source record IDs, matched terms, and versioning",
    )
    source: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Originating knowledge source (e.g. DDInter, openFDA, DailyMed)",
    )
    source_version: Optional[str] = Field(
        None,
        description="Version string of the evidence or rule source",
    )
    evidence_id: Optional[str] = Field(
        None,
        description="External evidence identifier if applicable",
    )

    @model_validator(mode="before")
    @classmethod
    def populate_provenance_from_trace(cls, data: Any) -> Any:
        if isinstance(data, dict):
            trace = data.get("trace")
            if isinstance(trace, dict):
                if data.get("evidence_id") is None and trace.get("evidence_id") is not None:
                    data["evidence_id"] = str(trace["evidence_id"])
                if data.get("source_version") is None and trace.get("source_version") is not None:
                    data["source_version"] = str(trace["source_version"])
        return data

    @property
    def is_demo_rule(self) -> bool:
        """Deterministically distinguish prototype demo rules from external validated evidence."""
        src = (self.source or "").upper()
        rid = (self.rule_id or "").upper()
        return "DEMO" in src or "DEMO" in rid or "AEGIS_HACKATHON" in src


class RiskAssessmentReport(BaseModel):
    """Container for the output of a risk detection evaluation."""

    patient_id: Optional[Union[int, str]] = None
    findings: List[Finding] = Field(default_factory=list)
    evaluated_medications_count: int = 0
    total_findings_count: int = 0
