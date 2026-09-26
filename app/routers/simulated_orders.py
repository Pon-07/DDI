from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import get_audit_ledger
from app.schemas import CosignRequest, CosignResponse, SimulatedOrderResponse
from agents.audit.ledger import AuditLedger

router = APIRouter(prefix="/simulated-orders", tags=["Simulated Orders"])


@router.get(
    "",
    response_model=List[Dict[str, Any]],
    summary="List all simulated orders from the audit trail",
)
def list_simulated_orders(
    patient_id: Optional[int] = Query(None, description="Optional patient ID filter"),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
):
    all_events = audit_ledger.get_events()
    events = [e for e in all_events if e.event_type == "SIMULATED_ORDER_CREATED"]
    cosigned_events = [e for e in all_events if e.event_type == "SIMULATED_ORDER_COSIGNED"]
    cosigned_ids = {e.payload.get("simulation_id") for e in cosigned_events if isinstance(e.payload, dict)}

    orders = []
    for ev in events:
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        if patient_id is not None and payload.get("patient_id") != patient_id:
            continue
        order_data = dict(payload)
        order_data["status"] = "cosigned" if payload.get("simulation_id") in cosigned_ids else "pending_cosign"
        orders.append(order_data)
    return orders


@router.get(
    "/{simulation_id}",
    response_model=Dict[str, Any],
    summary="Retrieve simulated order by simulation ID",
)
def get_simulated_order(
    simulation_id: str,
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
):
    all_events = audit_ledger.get_events()
    events = [e for e in all_events if e.event_type == "SIMULATED_ORDER_CREATED"]
    matching = None
    for ev in events:
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        if payload.get("simulation_id") == simulation_id:
            matching = dict(payload)
            break

    if not matching:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulated order '{simulation_id}' not found.",
        )

    cosigned_events = [e for e in all_events if e.event_type == "SIMULATED_ORDER_COSIGNED"]
    for ce in cosigned_events:
        c_payload = ce.payload if isinstance(ce.payload, dict) else {}
        if c_payload.get("simulation_id") == simulation_id:
            matching["status"] = f"cosigned_{c_payload.get('decision', 'approved')}"
            matching["cosigned_by"] = c_payload.get("cosigned_by")
            break

    return matching


@router.post(
    "/{simulation_id}/cosign",
    response_model=CosignResponse,
    status_code=status.HTTP_200_OK,
    summary="Record human clinician cosign for a simulated order",
)
def cosign_simulated_order(
    simulation_id: str,
    payload: CosignRequest,
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
):
    # Verify simulation exists in audit trail
    all_events = audit_ledger.get_events()
    events = [e for e in all_events if e.event_type == "SIMULATED_ORDER_CREATED"]
    matching = None
    for ev in events:
        p = ev.payload if isinstance(ev.payload, dict) else {}
        if p.get("simulation_id") == simulation_id:
            matching = p
            break

    if not matching:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulated order '{simulation_id}' not found in audit ledger.",
        )

    # Append clinician cosign event to cryptographic audit ledger
    record = audit_ledger.append_event(
        actor=payload.cosigned_by,
        event_type="SIMULATED_ORDER_COSIGNED",
        payload={
            "simulation_id": simulation_id,
            "cosigned_by": payload.cosigned_by,
            "decision": payload.decision,
            "clinical_notes": payload.clinical_notes,
            "requires_cosign": True,
            "auto_execute": False,
        },
    )

    return CosignResponse(
        simulation_id=simulation_id,
        cosigned_by=payload.cosigned_by,
        decision=payload.decision,
        status=f"cosigned_{payload.decision}",
        requires_cosign=True,
        auto_execute=False,
        message="Simulated order cosign recorded. Prescriptions are NOT automatically executed or altered.",
        audit_hash=record.hash,
    )
