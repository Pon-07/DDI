from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.schemas import MedicationCreate, MedicationResponse
from database.database import get_db
from engine.drug_service import (
    is_medication_recognized,
    normalize_medication_name,
    search_local_medications,
    validate_medication_input,
)
from models import Medication, Patient

router = APIRouter(tags=["Medications & Knowledge Lookup"])


class DrugValidateRequest(BaseModel):
    drug_name: str = Field(..., description="Medication name to validate against local knowledge base")


class DrugValidateResponse(BaseModel):
    is_valid: bool
    status: str
    message: str
    raw_input: str
    normalized_input: str
    possible_actions: List[str] = []
    details: Optional[Dict[str, Any]] = None


# =========================================================================
# DRUG SEARCH & VALIDATION (100% OFFLINE / LOCAL KNOWLEDGE BASE)
# =========================================================================

@router.get(
    "/drugs/search",
    summary="Search medication names in local knowledge base (autocomplete/suggestions)",
)
@router.get(
    "/medications/search",
    summary="Search medication names in local knowledge base (autocomplete/suggestions)",
)
def search_drugs(
    q: str = Query("", description="Medication search prefix or keyword"),
    limit: int = Query(12, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """
    Returns candidate medications from the local offline knowledge base.
    Zero external internet API calls.
    """
    return search_local_medications(db=db, query=q, limit=limit)


@router.post(
    "/drugs/validate",
    response_model=DrugValidateResponse,
    summary="Validate medication against local knowledge base before clinical evaluation",
)
@router.post(
    "/medications/validate",
    response_model=DrugValidateResponse,
    summary="Validate medication against local knowledge base before clinical evaluation",
)
def validate_drug(
    payload: DrugValidateRequest,
    db: Session = Depends(get_db),
):
    """
    Checks if a medication exists in the local offline database/catalog.
    Rejects arbitrary or unrecognized medications with helpful clinical guidance.
    """
    result = validate_medication_input(db=db, raw_name=payload.drug_name)
    return DrugValidateResponse(**result)


# =========================================================================
# PATIENT MEDICATION CRUD
# =========================================================================

@router.post(
    "/patients/{patient_id}/medications",
    response_model=MedicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Prescribe/add medication for a patient",
)
def create_medication_for_patient(
    patient_id: int,
    payload: MedicationCreate,
    db: Session = Depends(get_db),
):
    # Verify patient exists
    patient = db.execute(
        select(Patient).where(Patient.id == patient_id)
    ).scalar_one_or_none()
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient with ID {patient_id} not found.",
        )

    # Validate drug exists in local medication knowledge base
    if not is_medication_recognized(db, payload.drug_name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Medication '{payload.drug_name}' not recognized in the local medication knowledge base.",
        )

    medication = Medication(
        patient_id=patient_id,
        drug_name=payload.drug_name,
        rxnorm_code=payload.rxnorm_code,
        dose=payload.dose,
        dose_unit=payload.dose_unit,
        route=payload.route,
        frequency=payload.frequency,
        status=payload.status,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    db.add(medication)
    db.commit()
    db.refresh(medication)
    return medication


@router.get(
    "/patients/{patient_id}/medications",
    response_model=List[MedicationResponse],
    summary="Retrieve all medications for a patient",
)
def list_medications_for_patient(
    patient_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    # Verify patient exists
    patient = db.execute(
        select(Patient).where(Patient.id == patient_id)
    ).scalar_one_or_none()
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient with ID {patient_id} not found.",
        )

    stmt = (
        select(Medication)
        .where(Medication.patient_id == patient_id)
        .offset(skip)
        .limit(limit)
    )
    medications = db.execute(stmt).scalars().all()
    return medications


@router.get(
    "/medications/{medication_id}",
    response_model=MedicationResponse,
    summary="Retrieve a medication by ID",
)
def get_medication(
    medication_id: int,
    db: Session = Depends(get_db),
):
    stmt = select(Medication).where(Medication.id == medication_id)
    medication = db.execute(stmt).scalar_one_or_none()
    if not medication:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Medication with ID {medication_id} not found.",
        )
    return medication
