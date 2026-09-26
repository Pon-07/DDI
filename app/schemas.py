from datetime import date, datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class PatientBase(BaseModel):
    patient_identifier: str = Field(..., min_length=1, max_length=100, description="Unique patient identifier / MRN")
    name: str = Field(..., min_length=1, max_length=255, description="Full patient name")
    date_of_birth: Optional[date] = Field(None, description="Date of birth")
    sex: Optional[str] = Field(None, max_length=50, description="Sex / Gender")
    allergy_information: Optional[str] = Field(None, description="Documented patient allergies")


class PatientCreate(PatientBase):
    pass


class PatientResponse(PatientBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class MedicationBase(BaseModel):
    drug_name: str = Field(..., min_length=1, max_length=255, description="Name of the prescribed drug")
    rxnorm_code: Optional[str] = Field(None, max_length=50, description="RxNorm identifier")
    dose: Optional[str] = Field(None, max_length=100, description="Dose amount")
    dose_unit: Optional[str] = Field(None, max_length=50, description="Dose measurement unit")
    route: Optional[str] = Field(None, max_length=50, description="Route of administration")
    frequency: Optional[str] = Field(None, max_length=100, description="Dosing frequency")
    status: Optional[str] = Field("active", max_length=50, description="Medication status")
    start_date: Optional[datetime] = Field(None, description="Medication start datetime")
    end_date: Optional[datetime] = Field(None, description="Medication end datetime")


class MedicationCreate(MedicationBase):
    pass


class MedicationResponse(MedicationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    created_at: datetime


class LabBase(BaseModel):
    test_name: str = Field(..., min_length=1, max_length=255, description="Name of laboratory test")
    value: str = Field(..., min_length=1, max_length=100, description="Measured lab value / result")
    unit: Optional[str] = Field(None, max_length=50, description="Unit of measurement")
    reference_range: Optional[str] = Field(None, max_length=100, description="Laboratory reference range")
    measured_at: Optional[datetime] = Field(None, description="Timestamp of test measurement")


class LabCreate(LabBase):
    pass


class LabResponse(LabBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    created_at: datetime


class OrderBase(BaseModel):
    drug_name: str = Field(..., min_length=1, max_length=255, description="Name of ordered medication")
    medication_id: Optional[int] = Field(None, description="Optional foreign key to existing medication record")
    dose: Optional[str] = Field(None, max_length=100, description="Dose amount")
    dose_unit: Optional[str] = Field(None, max_length=50, description="Dose unit")
    route: Optional[str] = Field(None, max_length=50, description="Route of administration")
    frequency: Optional[str] = Field(None, max_length=100, description="Dosing frequency")
    status: Optional[str] = Field("pending", max_length=50, description="Order status")
    ordered_at: Optional[datetime] = Field(None, description="Timestamp of the order")


class OrderCreate(OrderBase):
    pass


class OrderResponse(OrderBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    ordered_at: datetime


class FindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: Optional[int] = None
    rule_id: str
    severity: str
    title: str
    description: Optional[str] = None
    action: Optional[str] = None
    inputs: Optional[Any] = None
    trace: Optional[Any] = None
    created_at: datetime


class PatientContextResponse(BaseModel):
    patient: PatientResponse
    medications: List[MedicationResponse]
    labs: List[LabResponse]
    orders: List[OrderResponse]
    findings: List[FindingResponse]


class EventSubmissionRequest(BaseModel):
    patient_id: int = Field(..., description="ID of the patient for the clinical event")
    event_type: str = Field(..., min_length=1, max_length=100, description="Type of clinical event (e.g. MEDICATION_PRESCRIBED, LAB_RESULT_RECORDED)")
    payload: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Event payload dictionary")
    new_medication_name: Optional[str] = Field(None, max_length=255, description="Optional new medication name to add to patient context")


class ResolutionCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    finding_id: Union[int, str]
    action_type: str
    description: str
    rationale: str
    source: str
    rule_id: Optional[str] = None
    source_version: Optional[str] = None
    evidence_id: Optional[str] = None
    requires_cosign: bool = True
    priority_score: Optional[float] = None


class SimulatedOrderResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    simulation_id: str
    finding_id: Union[int, str]
    patient_id: Optional[Union[int, str]] = None
    medication_id: Optional[Union[int, str]] = None
    drug_name: str
    proposed_action: str
    action_type: str
    dose: Optional[str] = None
    dose_unit: Optional[str] = None
    route: Optional[str] = None
    frequency: Optional[str] = None
    status: str = "pending_cosign"
    requires_cosign: bool = True
    auto_execute: bool = False
    is_simulated: bool = True
    rationale: str
    source: str
    rule_id: Optional[str] = None
    source_version: Optional[str] = None
    evidence_id: Optional[str] = None
    resolution_id: Optional[str] = None
    created_at: Optional[datetime] = None


class ExplanationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    finding_id: Union[int, str]
    rule_id: Optional[str] = None
    title: str
    summary: str
    medications: List[str] = Field(default_factory=list)
    patient_context: Optional[Dict[str, Any]] = None
    evidence_source: str
    source_version: Optional[str] = None
    evidence_id: Optional[str] = None
    severity: str = "undetermined"
    guidance: Optional[str] = None
    human_action_status: str = "Mandatory clinical cosign required"
    full_text: str
    is_fallback: bool = False
    is_llm_enhanced: bool = False
    is_deterministic_fallback: Optional[bool] = None

    def model_post_init(self, __context: Any) -> None:
        if self.is_deterministic_fallback is None:
            self.is_deterministic_fallback = not self.is_llm_enhanced


class ResolutionPipelineResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    finding_id: Union[int, str]
    finding_severity: str
    status: str
    raw_candidates: List[ResolutionCandidateResponse] = Field(default_factory=list)
    safety_filtered_candidates: List[ResolutionCandidateResponse] = Field(default_factory=list)
    ranked_candidates: List[ResolutionCandidateResponse] = Field(default_factory=list)
    simulated_order: Optional[SimulatedOrderResponse] = None
    explanation: Optional[ExplanationResponse] = None
    requires_cosign: bool = True
    review_reason: Optional[str] = None
    rule_id: Optional[str] = None
    source: Optional[str] = None
    source_version: Optional[str] = None
    evidence_id: Optional[str] = None


class EventSubmissionResponse(BaseModel):
    patient_id: int
    event_type: str
    affected_findings: List[FindingResponse]
    resolutions: List[ResolutionPipelineResponse]
    audit_logged: bool = True


class CosignRequest(BaseModel):
    cosigned_by: str = Field(..., min_length=1, max_length=100, description="Clinician name or identifier")
    decision: str = Field("approved", description="'approved' or 'rejected'")
    clinical_notes: Optional[str] = Field(None, description="Optional clinical rationale / notes")


class CosignResponse(BaseModel):
    simulation_id: str
    cosigned_by: str
    decision: str
    status: str
    requires_cosign: bool = True
    auto_execute: bool = False
    message: str
    audit_hash: str


class AuditRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: str
    actor: str
    event_type: str
    payload: Dict[str, Any]
    prev_hash: str
    hash: str


class ChainVerificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    is_valid: bool
    total_records: int
    errors: List[str] = Field(default_factory=list)
    tampered_record_id: Optional[int] = None


class SystemStatusResponse(BaseModel):
    status: str
    offline_capable: bool
    database: str
    rule_pack: str
    active_rules_count: int
    ollama_available: bool
    audit_chain_valid: bool
    total_audit_records: int




