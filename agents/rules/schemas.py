from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class SafetyRule(BaseModel):
    """
    Schema for a validated medication safety rule.
    Strictly preserves source and evidence provenance without inferring unvalidated severity or recommendations.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Unique identifier for the safety rule",
    )
    rule_type: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Type of safety rule (e.g., drug_interaction, contraindication, label_warning)",
    )
    drug_a: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Primary drug name or active ingredient",
    )
    drug_b: Optional[str] = Field(
        None,
        max_length=255,
        description="Secondary interacting drug name or active ingredient (if applicable)",
    )
    source: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Originating validated knowledge source (e.g., DDInter, openFDA, DailyMed)",
    )
    source_version: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Version or release timestamp of the evidence source",
    )
    evidence_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Exact identifier of the evidence item in the source dataset",
    )
    evidence_text: str = Field(
        ...,
        min_length=1,
        description="Verbatim excerpt or statement from the evidence source",
    )
    severity: Optional[str] = Field(
        None,
        max_length=50,
        description="Validated severity if explicitly provided by source; otherwise None / unspecified",
    )
    action: Optional[str] = Field(
        None,
        description="Validated clinical guidance if explicitly provided by source; otherwise None",
    )
    status: str = Field(
        default="active",
        description="Lifecycle status of the rule (e.g., active, draft, deprecated, archived)",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when the rule was registered",
    )


class RulePack(BaseModel):
    """Container schema for a bundle of validated safety rules."""

    model_config = ConfigDict(extra="forbid")

    pack_name: str = Field(..., min_length=1, max_length=100)
    version: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = None
    rules: List[SafetyRule] = Field(default_factory=list)
