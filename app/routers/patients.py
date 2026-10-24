from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas import (
    FindingResponse,
    PatientContextResponse,
    PatientCreate,
    PatientResponse,
)
from database.database import get_db
from models import Finding, Lab, Medication, Order, Patient

router = APIRouter(prefix="/patients", tags=["Patients"])


@router.post(
    "",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new patient",
)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    existing = db.execute(
        select(Patient).where(Patient.patient_identifier == payload.patient_identifier)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Patient with identifier '{payload.patient_identifier}' already exists.",
        )

    patient = Patient(
        patient_identifier=payload.patient_identifier,
        name=payload.name,
        date_of_birth=payload.date_of_birth,
        sex=payload.sex,
        allergy_information=payload.allergy_information,
    )
    db.add(patient)
    try:
        db.commit()
        db.refresh(patient)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Integrity error creating patient record.",
        ) from exc

    return patient


@router.get(
    "",
    response_model=List[PatientResponse],
    summary="Retrieve all patients",
)
def list_patients(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    stmt = select(Patient).offset(skip).limit(limit)
    patients = db.execute(stmt).scalars().all()
    return patients


@router.get(
    "/{patient_id}",
    response_model=PatientResponse,
    summary="Retrieve a patient by ID",
)
def get_patient(patient_id: int, db: Session = Depends(get_db)):
    stmt = select(Patient).where(Patient.id == patient_id)
    patient = db.execute(stmt).scalar_one_or_none()
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient with ID {patient_id} not found.",
        )
    return patient


@router.get(
    "/{patient_id}/context",
    response_model=PatientContextResponse,
    summary="Retrieve complete patient clinical context",
)
def get_patient_context(patient_id: int, db: Session = Depends(get_db)):
    patient = db.execute(
        select(Patient).where(Patient.id == patient_id)
    ).scalar_one_or_none()
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient with ID {patient_id} not found.",
        )

    # Active/ordered medications
    meds = db.execute(
        select(Medication)
        .where(Medication.patient_id == patient_id)
        .order_by(Medication.id.asc())
    ).scalars().all()

    # Chronological labs
    labs = db.execute(
        select(Lab)
        .where(Lab.patient_id == patient_id)
        .order_by(Lab.measured_at.asc(), Lab.id.asc())
    ).scalars().all()

    # Orders
    orders = db.execute(
        select(Order)
        .where(Order.patient_id == patient_id)
        .order_by(Order.ordered_at.asc(), Order.id.asc())
    ).scalars().all()

    # Safety findings
    findings = db.execute(
        select(Finding)
        .where(Finding.patient_id == patient_id)
        .order_by(Finding.id.asc())
    ).scalars().all()

    return {
        "patient": patient,
        "medications": meds,
        "labs": labs,
        "orders": orders,
        "findings": findings,
    }


@router.get(
    "/{patient_id}/findings",
    response_model=List[FindingResponse],
    summary="Retrieve all safety findings for a patient",
)
def get_patient_findings(patient_id: int, db: Session = Depends(get_db)):
    patient = db.execute(
        select(Patient).where(Patient.id == patient_id)
    ).scalar_one_or_none()
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient with ID {patient_id} not found.",
        )

    findings = db.execute(
        select(Finding)
        .where(Finding.patient_id == patient_id)
        .order_by(Finding.id.asc())
    ).scalars().all()
    return findings

