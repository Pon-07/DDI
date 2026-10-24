from datetime import datetime
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
    rule_id: Optional[str] = Field(
        None,
        description="Reference identifier of the safety rule",
    )
    source_version: Optional[str] = Field(
        None,
        description="Version string of the evidence or rule source",
    )
    evidence_id: Optional[str] = Field(
        None,
        description="Reference identifier of the underlying evidence or safety rule",
    )
    requires_cosign: bool = Field(
        default=True,
        description="Safety enforcement flag. Must always be True for human verification.",
    )
    priority_score: Optional[float] = Field(
        default=None,
        description="Deterministic clinical priority ranking score",
    )

    @field_validator("requires_cosign")
    @classmethod
    def enforce_cosign_mandatory(cls, v: bool) -> bool:
        if not v:
            raise ValueError("requires_cosign must always be True. Automatic execution is not permitted.")
        return True

    @property
    def is_demo_rule(self) -> bool:
        """Deterministically distinguish prototype demo rules from external validated evidence."""
        src = (self.source or "").upper()
        rid = (self.rule_id or "").upper()
        return "DEMO" in src or "DEMO" in rid or "AEGIS_HACKATHON" in src


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


class SimulatedOrder(BaseModel):
    """
    Cosign-ready simulated order proposal generated strictly from validated safety candidates.
    Never auto-executed; mandatory clinical human cosign is enforced.
    Preserves existing medication parameters without inventing unvalidated doses or frequencies.
    """

    model_config = ConfigDict(extra="forbid")

    simulation_id: str = Field(
        ...,
        description="Unique deterministic identifier for this simulated order proposal",
    )
    finding_id: Union[int, str] = Field(
        ...,
        description="Associated safety finding identifier",
    )
    patient_id: Optional[Union[int, str]] = Field(
        None,
        description="Patient identifier if available from finding or patient context",
    )
    medication_id: Optional[Union[int, str]] = Field(
        None,
        description="Identifier of the existing active medication record if applicable",
    )
    drug_name: str = Field(
        ...,
        min_length=1,
        description="Target medication name subject to the simulated action",
    )
    proposed_action: str = Field(
        ...,
        min_length=1,
        description="Validated clinical guidance or recommendation (e.g. hold, monitor, review)",
    )
    action_type: str = Field(
        ...,
        min_length=1,
        description="Safety rule type or category (e.g., drug_interaction, renal_risk)",
    )
    dose: Optional[str] = Field(
        None,
        description="Preserved dose from existing medication record. Never invented or inferred.",
    )
    dose_unit: Optional[str] = Field(
        None,
        description="Preserved dose unit from existing medication record. Never invented.",
    )
    route: Optional[str] = Field(
        None,
        description="Preserved route from existing medication record. Never invented.",
    )
    frequency: Optional[str] = Field(
        None,
        description="Preserved frequency from existing medication record. Never invented.",
    )
    status: str = Field(
        default="pending_cosign",
        description="Simulated order lifecycle status. Must remain pending clinician cosign.",
    )
    requires_cosign: bool = Field(
        default=True,
        description="Safety enforcement flag. Must always be True; automatic execution is prohibited.",
    )
    auto_execute: bool = Field(
        default=False,
        description="Safety enforcement flag. Must always be False.",
    )
    is_simulated: bool = Field(
        default=True,
        description="Explicit flag marking this order as a non-executed clinical simulation.",
    )
    rationale: str = Field(
        ...,
        min_length=1,
        description="Audit and clinical justification with provenance trace",
    )
    source: str = Field(
        ...,
        min_length=1,
        description="Originating validated knowledge or rule source",
    )
    rule_id: Optional[str] = Field(
        None,
        description="Associated safety rule identifier",
    )
    source_version: Optional[str] = Field(
        None,
        description="Version string of the knowledge source or rule pack",
    )
    evidence_id: Optional[str] = Field(
        None,
        description="Underlying evidence identifier from validated dataset",
    )
    resolution_id: Optional[str] = Field(
        None,
        description="Identifier of the originating resolution candidate or process",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp of simulation generation",
    )

    @property
    def is_demo_rule(self) -> bool:
        """Deterministically distinguish prototype demo rules from external validated evidence."""
        src = (self.source or "").upper()
        rid = (self.rule_id or "").upper()
        return "DEMO" in src or "DEMO" in rid or "AEGIS_HACKATHON" in src

    @field_validator("requires_cosign")
    @classmethod
    def enforce_cosign_mandatory(cls, v: bool) -> bool:
        if not v:
            raise ValueError("requires_cosign must always be True. Automatic execution is not permitted.")
        return True

    @field_validator("auto_execute")
    @classmethod
    def enforce_no_auto_execute(cls, v: bool) -> bool:
        if v:
            raise ValueError("auto_execute must always be False. Prescription changes require human cosign.")
        return False

    @field_validator("is_simulated")
    @classmethod
    def enforce_simulated_only(cls, v: bool) -> bool:
        if not v:
            raise ValueError("is_simulated must be True for simulated orders.")
        return True

    @field_validator("status")
    @classmethod
    def enforce_pending_cosign_status(cls, v: str) -> str:
        allowed = {"pending_cosign", "simulated_pending_cosign", "draft_simulated", "pending"}
        if v.lower() not in allowed:
            raise ValueError(f"Status '{v}' is not permitted for simulated orders. Must require clinician cosign.")
        return v

    def to_orm_order(self) -> Any:
        """
        Convert to SQLAlchemy Order model with pending status.
        Never auto-executes and never modifies existing prescriptions.
        """
        from models.models import Order

        p_id = int(self.patient_id) if self.patient_id is not None and str(self.patient_id).isdigit() else None
        m_id = int(self.medication_id) if self.medication_id is not None and str(self.medication_id).isdigit() else None

        return Order(
            patient_id=p_id,
            medication_id=m_id,
            drug_name=self.drug_name,
            dose=self.dose,
            dose_unit=self.dose_unit,
            route=self.route,
            frequency=self.frequency,
            status=self.status,
        )


from agents.explicator.templates import ExplanationReport


class ResolutionPipelineResult(BaseModel):
    """
    End-to-end audit result of the resolution pipeline for a single safety finding:
    Finding -> deterministic resolution -> safety-filtered candidate actions -> ranked candidates -> cosign-ready simulated order.
    """

    model_config = ConfigDict(extra="forbid")

    finding_id: Union[int, str]
    finding_severity: str = Field(
        default="undetermined",
        description="Severity level of the evaluated finding",
    )
    status: str = Field(
        ...,
        description="Resolution status: 'actionable' if validated action exists, otherwise 'requires_human_review'",
    )
    raw_candidates: List[ResolutionCandidate] = Field(
        default_factory=list,
        description="Unfiltered resolution candidates produced by deterministic rule matching",
    )
    safety_filtered_candidates: List[ResolutionCandidate] = Field(
        default_factory=list,
        description="Validated candidates passing strict safety filtering",
    )
    ranked_candidates: List[ResolutionCandidate] = Field(
        default_factory=list,
        description="Safety-filtered candidates ranked deterministically by clinical urgency and rule specificity",
    )
    simulated_order: Optional[SimulatedOrder] = Field(
        None,
        description="Cosign-ready simulated order for the highest-priority candidate (None if requires_human_review)",
    )
    explanation: Optional[ExplanationReport] = Field(
        None,
        description="Deterministic or validated explanation report for the finding and resolution",
    )
    requires_cosign: bool = Field(
        default=True,
        description="Safety enforcement flag. Always True.",
    )
    review_reason: Optional[str] = Field(
        None,
        description="Explicit explanation when human review is required or no validated action exists",
    )
    rule_id: Optional[str] = Field(
        None,
        description="Associated safety rule identifier",
    )
    source: Optional[str] = Field(
        None,
        description="Originating validated knowledge or rule source",
    )
    source_version: Optional[str] = Field(
        None,
        description="Version string of the knowledge source or rule pack",
    )
    evidence_id: Optional[str] = Field(
        None,
        description="Underlying evidence identifier from validated dataset",
    )

    @property
    def is_demo_rule(self) -> bool:
        """Deterministically distinguish prototype demo rules from external validated evidence."""
        src = (self.source or "").upper()
        rid = (self.rule_id or "").upper()
        return "DEMO" in src or "DEMO" in rid or "AEGIS_HACKATHON" in src

    @field_validator("requires_cosign")
    @classmethod
    def enforce_cosign_mandatory(cls, v: bool) -> bool:
        if not v:
            raise ValueError("requires_cosign must always be True.")
        return True
