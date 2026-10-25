"""
Core business logic and audit persistence for Pharmacist-to-Doctor Medication Substitutions.
Ensures deterministic safety, audit ledger provenance, and single source of truth across
REST API routers and dashboard endpoints.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.models import Lab, Medication, Order, Patient, SubstitutionRequest
from engine.auth.models import User


def serialize_substitution_request(db: Session, request: SubstitutionRequest) -> Dict[str, Any]:
    """Serialize request details from referenced clinical records, never untrusted client names."""
    patient = db.get(Patient, request.patient_id)
    order = db.get(Order, request.order_id)
    medication = db.get(Medication, request.medication_id)
    if patient is None or order is None or medication is None:
        raise HTTPException(status_code=409, detail="Substitution request references unavailable clinical records.")

    prescribed = " ".join(
        part for part in (
            order.drug_name,
            " ".join(part for part in (order.dose, order.dose_unit) if part),
            order.route,
            order.frequency,
        ) if part
    )
    return {
        "request_id": request.id,
        "case_id": patient.patient_identifier,
        "patient_id": patient.id,
        "patient_identifier": patient.patient_identifier,
        "patient_name": patient.name,
        "order_id": order.id,
        "medication_id": medication.id,
        "prescribed_medicine": order.drug_name,
        "original_prescription": prescribed,
        "prescribed_brand": order.drug_name,
        "suggested_brand": request.suggested_brand,
        "reason": request.reason,
        "pharmacist_note": request.pharmacist_notes or "",
        "pharmacist_name": request.requested_by_name,
        "request_date": request.created_at.isoformat() if request.created_at else None,
        "status": request.status,
        "doctor_notes": request.doctor_notes,
        "decided_by": request.decided_by_name,
        "decided_at": request.decided_at.isoformat() if request.decided_at else None,
    }


def submit_substitution_request(
    db: Session,
    audit_ledger: Any,
    current_user: User,
    req: Any,
) -> Dict[str, Any]:
    """
    Persist a pharmacist request against an existing patient, order, and medication.
    Submission never modifies the prescription or marks it ready for dispensing.
    """
    suggested_brand = req.suggested_brand.strip()
    reason = req.reason.strip()
    if not suggested_brand or not reason:
        raise HTTPException(status_code=422, detail="Suggested brand and reason must contain non-whitespace text.")

    patient = db.get(Patient, req.patient_id)
    order = db.get(Order, req.order_id)
    medication = db.get(Medication, req.medication_id)
    if patient is None or order is None or medication is None:
        raise HTTPException(status_code=404, detail="Patient, prescription, or medication record was not found.")
    if order.patient_id != patient.id or medication.patient_id != patient.id:
        raise HTTPException(status_code=422, detail="Patient, prescription, and medication references do not match.")
    if order.medication_id != medication.id:
        raise HTTPException(status_code=422, detail="The medication is not the medication linked to this prescription.")
    if (order.status or "").strip().lower() not in {"", "active", "pending", "ordered"}:
        raise HTTPException(status_code=409, detail="Substitution requests are only allowed for an active prescription.")
    if (medication.status or "").strip().lower() not in {"", "active", "pending", "ordered"}:
        raise HTTPException(status_code=409, detail="Substitution requests are only allowed for an active medication.")

    def same_submission(existing: SubstitutionRequest) -> bool:
        return (
            existing.requested_by_user_id == current_user.id
            and existing.patient_id == patient.id
            and existing.order_id == order.id
            and existing.medication_id == medication.id
            and existing.suggested_brand.strip().casefold() == suggested_brand.casefold()
            and existing.reason.strip() == reason
            and (existing.pharmacist_notes or "").strip() == (req.pharmacist_notes or "").strip()
        )

    existing = db.execute(
        select(SubstitutionRequest).where(SubstitutionRequest.idempotency_key == req.idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        if not same_submission(existing):
            raise HTTPException(status_code=409, detail="Idempotency key was already used for a different request.")
        return {
            "request_id": existing.id,
            "case_id": patient.patient_identifier,
            "status": existing.status,
            "message": "This substitution request was already recorded.",
            "audit_event_id": None,
            "audit_hash": None,
        }

    pending = db.execute(
        select(SubstitutionRequest).where(
            SubstitutionRequest.order_id == order.id,
            SubstitutionRequest.status == "pending",
        )
    ).scalar_one_or_none()
    if pending is not None:
        if same_submission(pending):
            return {
                "request_id": pending.id,
                "case_id": patient.patient_identifier,
                "status": pending.status,
                "message": "A pending request for this prescription is already recorded.",
                "audit_event_id": None,
                "audit_hash": None,
            }
        raise HTTPException(status_code=409, detail="This prescription already has a pending substitution request.")

    request = SubstitutionRequest(
        patient_id=patient.id,
        order_id=order.id,
        medication_id=medication.id,
        idempotency_key=req.idempotency_key,
        suggested_brand=suggested_brand,
        reason=reason,
        pharmacist_notes=req.pharmacist_notes.strip() if req.pharmacist_notes else None,
        requested_by_user_id=current_user.id,
        requested_by_name=current_user.full_name,
        status="pending",
    )
    db.add(request)
    try:
        db.commit()
        db.refresh(request)
    except IntegrityError as exc:
        db.rollback()
        existing = db.execute(
            select(SubstitutionRequest).where(
                (SubstitutionRequest.idempotency_key == req.idempotency_key)
                | (
                    (SubstitutionRequest.order_id == order.id)
                    & (SubstitutionRequest.status == "pending")
                )
            )
        ).scalars().first()
        if existing is not None and same_submission(existing):
            return {
                "request_id": existing.id,
                "case_id": patient.patient_identifier,
                "status": existing.status,
                "message": "This substitution request was already recorded.",
                "audit_event_id": None,
                "audit_hash": None,
            }
        raise HTTPException(status_code=409, detail="A duplicate or conflicting request already exists.") from exc

    event = audit_ledger.append_event(
        actor=current_user.full_name,
        event_type="PHARMACY_SUBSTITUTION_REQUESTED",
        payload={
            "request_id": request.id,
            "patient_id": patient.id,
            "order_id": order.id,
            "medication_id": medication.id,
            "suggested_brand": request.suggested_brand,
            "status": request.status,
        },
    )
    return {
        "request_id": request.id,
        "case_id": patient.patient_identifier,
        "status": request.status,
        "message": f"Substitution request for {request.suggested_brand} sent to the prescribing Doctor.",
        "audit_event_id": event.id,
        "audit_hash": event.hash,
    }


def decide_substitution_request(
    db: Session,
    audit_ledger: Any,
    current_user: User,
    req: Any,
) -> Dict[str, Any]:
    """
    Only an authorized Doctor may decide a persisted request, and only while it is pending.
    Clinicians cannot decide their own requests.
    """
    request = db.get(SubstitutionRequest, req.request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Substitution request was not found.")
    if request.requested_by_user_id == current_user.id:
        raise HTTPException(status_code=403, detail="A pharmacist cannot approve or reject their own request.")

    final_status = {
        "APPROVED": "approved",
        "REJECTED": "rejected",
        "CLARIFICATION_REQUIRED": "clarification_required",
    }[req.decision]
    now = datetime.utcnow()
    result = db.execute(
        update(SubstitutionRequest)
        .where(
            SubstitutionRequest.id == request.id,
            SubstitutionRequest.status == "pending",
        )
        .values(
            status=final_status,
            doctor_notes=req.doctor_notes.strip() if req.doctor_notes else None,
            decided_by_user_id=current_user.id,
            decided_by_name=current_user.full_name,
            decided_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Only a pending substitution request can be decided.")
    db.commit()
    db.refresh(request)

    event_type = {
        "approved": "DOCTOR_SUBSTITUTION_APPROVED",
        "rejected": "DOCTOR_SUBSTITUTION_REJECTED",
        "clarification_required": "DOCTOR_CLARIFICATION_REQUESTED",
    }[final_status]
    event = audit_ledger.append_event(
        actor=current_user.full_name,
        event_type=event_type,
        payload={
            "request_id": request.id,
            "patient_id": request.patient_id,
            "order_id": request.order_id,
            "decision": req.decision,
            "status": final_status,
        },
    )
    return {
        "request_id": request.id,
        "status": final_status,
        "decision": req.decision,
        "message": f"Substitution request {final_status.replace('_', ' ')} by Doctor.",
        "audit_event_id": event.id,
        "audit_hash": event.hash,
        "ready_for_dispensing": final_status == "approved",
    }


def get_substitution_request_by_id(db: Session, request_id: int) -> Dict[str, Any]:
    """Retrieve single substitution request by ID."""
    request = db.get(SubstitutionRequest, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Substitution request was not found.")
    return serialize_substitution_request(db, request)


def list_substitution_requests(
    db: Session,
    patient_id: Optional[int] = None,
    order_id: Optional[int] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List substitution requests with optional filtering."""
    query = select(SubstitutionRequest).order_by(SubstitutionRequest.created_at.desc())
    if patient_id is not None:
        query = query.where(SubstitutionRequest.patient_id == patient_id)
    if order_id is not None:
        query = query.where(SubstitutionRequest.order_id == order_id)
    if status is not None:
        query = query.where(SubstitutionRequest.status == status.strip().lower())
    requests = db.execute(query).scalars().all()
    return [serialize_substitution_request(db, r) for r in requests]


def get_patient_substitution_history(
    db: Session,
    patient_identifier_or_id: Union[int, str],
) -> List[Dict[str, Any]]:
    """Retrieve full substitution history for a patient."""
    if isinstance(patient_identifier_or_id, int) or (isinstance(patient_identifier_or_id, str) and patient_identifier_or_id.isdigit()):
        patient = db.get(Patient, int(patient_identifier_or_id))
    else:
        patient = db.execute(
            select(Patient).where(Patient.patient_identifier == str(patient_identifier_or_id).strip())
        ).scalar_one_or_none()
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient record was not found.")
    return list_substitution_requests(db, patient_id=patient.id)
