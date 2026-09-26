from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies import get_event_service
from app.schemas import EventSubmissionRequest, EventSubmissionResponse
from database.database import get_db
from engine.drug_service import is_medication_recognized
from engine.event_service import MedicationEventService
from models import Patient

router = APIRouter(prefix="/events", tags=["Events"])


@router.post(
    "",
    response_model=EventSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a patient medication, lab, or clinical event",
)
def submit_event(
    payload: EventSubmissionRequest,
    db: Session = Depends(get_db),
    event_service: MedicationEventService = Depends(get_event_service),
):
    # Verify patient exists
    patient = db.execute(
        select(Patient).where(Patient.id == payload.patient_id)
    ).scalar_one_or_none()
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient with ID {payload.patient_id} not found.",
        )

    # Validate drug name if a medication prescription event is submitted
    payload_dict = payload.payload or {}
    med_name = payload.new_medication_name or payload_dict.get("drug_name") or payload_dict.get("medication_name")
    if (payload.event_type in ("MEDICATION_PRESCRIBED", "MEDICATION_ORDERED") or payload.new_medication_name) and med_name:
        if not is_medication_recognized(db, str(med_name)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Medication '{med_name}' not recognized in the local medication knowledge base.",
            )

    try:
        affected_findings, pipeline_results = event_service.process_event_with_resolutions(
            db=db,
            patient_id=payload.patient_id,
            event_type=payload.event_type,
            payload=payload.payload or {},
            new_medication_name=payload.new_medication_name,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error executing event pipeline: {str(exc)}",
        ) from exc

    return {
        "patient_id": payload.patient_id,
        "event_type": payload.event_type,
        "affected_findings": affected_findings,
        "resolutions": [res.model_dump() for res in pipeline_results],
        "audit_logged": True,
    }
