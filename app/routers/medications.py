from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.schemas import MedicationCreate, MedicationResponse
from database.database import get_db
from models import Medication, Patient

router = APIRouter(tags=["Medications"])


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
