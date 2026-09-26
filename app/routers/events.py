from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies import get_event_service
from app.schemas import EventSubmissionRequest, EventSubmissionResponse
from database.database import get_db
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
