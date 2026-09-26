from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies import get_explicator_service, get_resolution_engine
from app.schemas import (
    ExplanationResponse,
    FindingResponse,
    ResolutionPipelineResponse,
    SimulatedOrderResponse,
)
from database.database import get_db
from models import Finding, Medication

router = APIRouter(prefix="/findings", tags=["Findings"])


@router.get(
    "",
    response_model=List[FindingResponse],
    summary="List risk findings (optionally filtered by patient_id)",
)
def list_findings(
    patient_id: Optional[int] = Query(None, description="Filter findings by patient ID"),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    stmt = select(Finding)
    if patient_id is not None:
        stmt = stmt.where(Finding.patient_id == patient_id)
    stmt = stmt.offset(skip).limit(limit)
    findings = db.execute(stmt).scalars().all()
    return findings


@router.get(
    "/{finding_id}",
    response_model=FindingResponse,
    summary="Retrieve a risk finding by ID",
)
def get_finding(
    finding_id: int,
    db: Session = Depends(get_db),
):
    stmt = select(Finding).where(Finding.id == finding_id)
    finding = db.execute(stmt).scalar_one_or_none()
    if not finding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Finding with ID {finding_id} not found.",
        )
    return finding


@router.get(
    "/{finding_id}/resolution",
    response_model=ResolutionPipelineResponse,
    summary="Retrieve deterministic resolution for a finding",
)
def get_finding_resolution(
    finding_id: int,
    db: Session = Depends(get_db),
    resolution_engine=Depends(get_resolution_engine),
):
    stmt = select(Finding).where(Finding.id == finding_id)
    finding = db.execute(stmt).scalar_one_or_none()
    if not finding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Finding with ID {finding_id} not found.",
        )

    # Fetch active medications for context if patient_id is present
    active_meds = []
    if finding.patient_id:
        med_stmt = select(Medication).where(
            Medication.patient_id == finding.patient_id,
            Medication.status.in_(["active", "pending", "ordered", None]),
        )
        active_meds = list(db.execute(med_stmt).scalars().all())

    pipeline_results = resolution_engine.resolve_findings_pipeline(
        findings=[finding],
        existing_medications=active_meds,
    )
    if not pipeline_results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unable to resolve finding {finding_id}.",
        )

    return pipeline_results[0].model_dump()


@router.get(
    "/{finding_id}/explanation",
    response_model=ExplanationResponse,
    summary="Retrieve deterministic explanation report for a finding",
)
def get_finding_explanation(
    finding_id: int,
    db: Session = Depends(get_db),
    explicator=Depends(get_explicator_service),
):
    stmt = select(Finding).where(Finding.id == finding_id)
    finding = db.execute(stmt).scalar_one_or_none()
    if not finding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Finding with ID {finding_id} not found.",
        )

    report = explicator.explicate(finding)
    return report.model_dump()


@router.get(
    "/{finding_id}/simulated-order",
    response_model=SimulatedOrderResponse,
    summary="Retrieve cosign-ready simulated order for a finding",
)
def get_finding_simulated_order(
    finding_id: int,
    db: Session = Depends(get_db),
    resolution_engine=Depends(get_resolution_engine),
):
    stmt = select(Finding).where(Finding.id == finding_id)
    finding = db.execute(stmt).scalar_one_or_none()
    if not finding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Finding with ID {finding_id} not found.",
        )

    active_meds = []
    if finding.patient_id:
        med_stmt = select(Medication).where(
            Medication.patient_id == finding.patient_id,
            Medication.status.in_(["active", "pending", "ordered", None]),
        )
        active_meds = list(db.execute(med_stmt).scalars().all())

    pipeline_results = resolution_engine.resolve_findings_pipeline(
        findings=[finding],
        existing_medications=active_meds,
    )
    if not pipeline_results or not pipeline_results[0].simulated_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No cosign-ready simulated order available for finding {finding_id}. Action requires human clinical review.",
        )

    return pipeline_results[0].simulated_order.model_dump()
