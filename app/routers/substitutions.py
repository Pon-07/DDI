"""
REST API Router for Pharmacist-to-Doctor Medication Substitution Workflow.
Provides structured JSON endpoints for:
- Pharmacist substitution request submission
- Doctor review, approval, rejection, and clarification requests
- Querying active and historical substitution requests
- Audited traceability via SHA-256 cryptographic ledger
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies import get_audit_ledger
from app.schemas import (
    SubstitutionDecisionRequest,
    SubstitutionDecisionResponse,
    SubstitutionDetailResponse,
    SubstitutionRequestCreate,
    SubstitutionRequestResponse,
)
from database.database import get_db
from engine.auth.models import User
from engine.auth.dependencies import require_role
from engine.substitution_service import (
    decide_substitution_request,
    get_patient_substitution_history,
    get_substitution_request_by_id,
    list_substitution_requests,
    submit_substitution_request,
)

router = APIRouter(prefix="/api/substitutions", tags=["Substitutions"])


@router.post(
    "/request",
    response_model=SubstitutionRequestResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit Pharmacist Medication Substitution Request",
)
@router.post(
    "",
    response_model=SubstitutionRequestResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit Pharmacist Medication Substitution Request (REST alias)",
)
def create_substitution_request_endpoint(
    req: SubstitutionRequestCreate,
    db: Session = Depends(get_db),
    audit_ledger=Depends(get_audit_ledger),
    current_user: User = Depends(require_role(["Clinical Pharmacist"])),
):
    """
    Submit a brand substitution request for doctor review.
    Enforces clinical role authorization (Clinical Pharmacist only).
    Idempotent submission: returns existing request if same idempotency key is submitted.
    Does NOT modify the patient prescription until authorized.
    """
    return submit_substitution_request(db, audit_ledger, current_user, req)


@router.post(
    "/decision",
    response_model=SubstitutionDecisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Doctor Decision on Medication Substitution Request",
)
def process_substitution_decision_endpoint(
    req: SubstitutionDecisionRequest,
    db: Session = Depends(get_db),
    audit_ledger=Depends(get_audit_ledger),
    current_user: User = Depends(require_role(["Doctor"])),
):
    """
    Approve, reject, or request clarification on a pending substitution request.
    Enforces clinical role authorization (Doctor only).
    Forbids self-decision if the requesting clinician attempts to decide their own request.
    """
    return decide_substitution_request(db, audit_ledger, current_user, req)


@router.post(
    "/{request_id}/decision",
    response_model=SubstitutionDecisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Doctor Decision on Medication Substitution Request by ID",
)
def process_substitution_decision_by_path_endpoint(
    request_id: int,
    req: SubstitutionDecisionRequest,
    db: Session = Depends(get_db),
    audit_ledger=Depends(get_audit_ledger),
    current_user: User = Depends(require_role(["Doctor"])),
):
    """Doctor decision endpoint accepting request_id in URL path."""
    if req.request_id != request_id:
        req = SubstitutionDecisionRequest(
            request_id=request_id,
            decision=req.decision,
            doctor_notes=req.doctor_notes,
        )
    return decide_substitution_request(db, audit_ledger, current_user, req)


@router.get(
    "",
    response_model=List[SubstitutionDetailResponse],
    status_code=status.HTTP_200_OK,
    summary="List Substitution Requests with Optional Filters",
)
def list_substitutions_endpoint(
    patient_id: Optional[int] = Query(None, description="Filter by patient database ID"),
    order_id: Optional[int] = Query(None, description="Filter by prescription order ID"),
    status: Optional[str] = Query(None, description="Filter by status (pending, approved, rejected, clarification_required)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["Doctor", "Clinical Pharmacist", "Admin"])),
):
    """Retrieve filtered list of substitution requests for clinicians."""
    return list_substitution_requests(db, patient_id=patient_id, order_id=order_id, status=status)


@router.get(
    "/{request_id}",
    response_model=SubstitutionDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Substitution Request by ID",
)
def get_substitution_endpoint(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["Doctor", "Clinical Pharmacist", "Admin"])),
):
    """Retrieve detailed clinical information for a single substitution request."""
    return get_substitution_request_by_id(db, request_id)


@router.get(
    "/patient/{patient_id}",
    response_model=List[SubstitutionDetailResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Patient Substitution Request History by Patient ID",
)
def get_patient_history_by_id_endpoint(
    patient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["Doctor", "Clinical Pharmacist", "Admin"])),
):
    """Retrieve full substitution history for a patient by database ID."""
    return get_patient_substitution_history(db, patient_id)


@router.get(
    "/history/{patient_identifier}",
    response_model=List[SubstitutionDetailResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Patient Substitution Request History by Identifier",
)
def get_patient_history_by_identifier_endpoint(
    patient_identifier: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["Doctor", "Clinical Pharmacist", "Admin"])),
):
    """Retrieve full substitution history for a patient by clinical identifier (e.g., DEMO-PT-002)."""
    return get_patient_substitution_history(db, patient_identifier)
