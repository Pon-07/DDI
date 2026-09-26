from typing import List, Optional
from fastapi import APIRouter, Depends, Query

from app.dependencies import get_audit_ledger
from app.schemas import AuditRecordResponse, ChainVerificationResponse
from agents.audit.ledger import AuditLedger

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get(
    "/verify",
    response_model=ChainVerificationResponse,
    summary="Cryptographically verify audit ledger hash-chain integrity",
)
def verify_audit_chain(
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
):
    result = audit_ledger.verify_chain()
    return ChainVerificationResponse(
        is_valid=result.is_valid,
        total_records=result.total_records,
        errors=result.errors,
        tampered_record_id=result.tampered_record_id,
    )


@router.get(
    "/events",
    response_model=List[AuditRecordResponse],
    summary="Retrieve audit trail events",
)
def get_audit_events(
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    actor: Optional[str] = Query(None, description="Filter by actor"),
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
):
    records = audit_ledger.get_events(limit=limit)
    if event_type:
        records = [r for r in records if r.event_type == event_type]
    if actor:
        records = [r for r in records if r.actor == actor]
    return records
