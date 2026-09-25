from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResolutionCandidate(BaseModel):
    """
    Candidate resolution proposal derived strictly from validated safety rules/evidence.
    Always requires clinical human cosign and never executes actions automatically.
    """

    model_config = ConfigDict(extra="forbid")

    finding_id: Union[int, str] = Field(
        ...,
        description="Identifier of the associated safety finding",
    )
    action_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Type or classification of validated guidance (e.g., 'monitor', 'avoid_concomitant_use', 'consult_specialist')",
    )
    description: str = Field(
        ...,
        min_length=1,
        description="Verbatim or validated action guidance from evidence source",
    )
    rationale: str = Field(
        ...,
        min_length=1,
        description="Clinical and evidence justification explaining why this candidate applies",
    )
    source: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Originating validated knowledge source (e.g., DDInter, openFDA, RulePack)",
    )
    evidence_id: Optional[str] = Field(
        None,
        description="Reference identifier of the underlying evidence or safety rule",
    )
    requires_cosign: bool = Field(
        default=True,
        description="Safety enforcement flag. Must always be True for human verification.",
    )

    @field_validator("requires_cosign")
    @classmethod
    def enforce_cosign_mandatory(cls, v: bool) -> bool:
        if not v:
            raise ValueError("requires_cosign must always be True. Automatic execution is not permitted.")
        return True


class ResolutionResult(BaseModel):
    """
    Summary report of resolution analysis for a given safety finding.
    Clearly marks cases where no validated action exists and human review is required.
    """

    model_config = ConfigDict(extra="forbid")

    finding_id: Union[int, str]
    status: str = Field(
        ...,
        description="Resolution status: 'actionable' if validated action exists, otherwise 'requires_human_review'",
    )
    candidates: List[ResolutionCandidate] = Field(
        default_factory=list,
        description="List of validated resolution candidates (empty if no validated action is present)",
    )
    review_reason: Optional[str] = Field(
        None,
        description="Explicit explanation when human review is required",
    )
