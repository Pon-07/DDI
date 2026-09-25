from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.schemas import LabCreate, LabResponse
from database.database import get_db
from models import Lab, Patient

router = APIRouter(tags=["Labs"])


@router.post(
    "/patients/{patient_id}/labs",
    response_model=LabResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a lab result for a patient",
)
def create_lab_for_patient(
    patient_id: int,
    payload: LabCreate,
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

    lab = Lab(
        patient_id=patient_id,
        test_name=payload.test_name,
        value=payload.value,
        unit=payload.unit,
        reference_range=payload.reference_range,
        measured_at=payload.measured_at,
    )
    db.add(lab)
    db.commit()
    db.refresh(lab)
    return lab


@router.get(
    "/patients/{patient_id}/labs",
    response_model=List[LabResponse],
    summary="Retrieve all lab results for a patient",
)
def list_labs_for_patient(
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
        select(Lab)
        .where(Lab.patient_id == patient_id)
        .offset(skip)
        .limit(limit)
    )
    labs = db.execute(stmt).scalars().all()
    return labs


@router.get(
    "/labs/{lab_id}",
    response_model=LabResponse,
    summary="Retrieve a lab result by ID",
)
def get_lab(
    lab_id: int,
    db: Session = Depends(get_db),
):
    stmt = select(Lab).where(Lab.id == lab_id)
    lab = db.execute(stmt).scalar_one_or_none()
    if not lab:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lab result with ID {lab_id} not found.",
        )
    return lab
