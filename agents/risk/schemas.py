from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


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


class RiskAssessmentReport(BaseModel):
    """Container for the output of a risk detection evaluation."""

    patient_id: Optional[Union[int, str]] = None
    findings: List[Finding] = Field(default_factory=list)
    evaluated_medications_count: int = 0
    total_findings_count: int = 0
