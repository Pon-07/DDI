from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.schemas import OrderCreate, OrderResponse
from database.database import get_db
from models import Medication, Order, Patient

router = APIRouter(tags=["Orders"])


@router.post(
    "/patients/{patient_id}/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a medication order for a patient",
)
def create_order_for_patient(
    patient_id: int,
    payload: OrderCreate,
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

    # If medication_id is provided, verify it exists and belongs to the patient
    if payload.medication_id is not None:
        med = db.execute(
            select(Medication).where(
                Medication.id == payload.medication_id,
                Medication.patient_id == patient_id,
            )
        ).scalar_one_or_none()
        if not med:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Medication with ID {payload.medication_id} not found for patient {patient_id}.",
            )

    order = Order(
        patient_id=patient_id,
        medication_id=payload.medication_id,
        drug_name=payload.drug_name,
        dose=payload.dose,
        dose_unit=payload.dose_unit,
        route=payload.route,
        frequency=payload.frequency,
        status=payload.status or "pending",
        ordered_at=payload.ordered_at or datetime.utcnow(),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.get(
    "/patients/{patient_id}/orders",
    response_model=List[OrderResponse],
    summary="Retrieve all orders for a patient",
)
def list_orders_for_patient(
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
        select(Order)
        .where(Order.patient_id == patient_id)
        .offset(skip)
        .limit(limit)
    )
    orders = db.execute(stmt).scalars().all()
    return orders


@router.get(
    "/orders/{order_id}",
    response_model=OrderResponse,
    summary="Retrieve an order by ID",
)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
):
    stmt = select(Order).where(Order.id == order_id)
    order = db.execute(stmt).scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with ID {order_id} not found.",
        )
    return order
