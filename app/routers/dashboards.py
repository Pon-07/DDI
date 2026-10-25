from engine.substitution_service import (
    decide_substitution_request,
    serialize_substitution_request,
    submit_substitution_request,
)
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from agents.audit.ledger import AuditLedger
from app.dependencies import get_audit_ledger, get_event_service, get_resolution_engine
from database.database import get_db
from engine.auth.dependencies import require_role
from engine.auth.models import User, UserSession
from engine.auth.providers.factory import get_sms_provider, list_available_providers
from engine.auth.service import AuthService
from models import Event, Finding, Lab, Medication, Order, Patient, SafetyRule, SubstitutionRequest

router = APIRouter(tags=["Role Dashboards & Patient History"])


# =========================================================================
# Database-backed patient history and dashboard serializers follow.

# REQUEST & RESPONSE PYDANTIC SCHEMAS
# =========================================================================

class PharmacistSubstitutionRequest(BaseModel):
    patient_id: int
    order_id: int
    medication_id: int
    idempotency_key: str = Field(min_length=16, max_length=128)
    suggested_brand: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=1000)
    pharmacist_notes: Optional[str] = Field(None, max_length=2000)


class DoctorSubstitutionDecisionRequest(BaseModel):
    request_id: int
    decision: Literal["APPROVED", "REJECTED", "CLARIFICATION_REQUIRED"]
    doctor_notes: Optional[str] = Field(None, max_length=2000)


def _normalize_medication_name(value: Optional[str]) -> str:
    return "".join(character.lower() for character in (value or "") if character.isalnum())


def _substitution_request_response(db: Session, request: SubstitutionRequest) -> Dict[str, Any]:
    """Serialize request details from the referenced clinical records, never client-supplied names."""
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


class PharmacistVerificationRequest(BaseModel):
    case_id: str
    patient_id: Optional[int] = None
    patient_name: Optional[str] = None
    patient_identifier: Optional[str] = None
    medication: str
    strength: str
    route: Optional[str] = "Oral"
    formulation: Optional[str] = None
    quantity: Optional[int] = None
    frequency: Optional[str] = None
    doctor_recommendation: Optional[str] = "Doctor prescription verified."
    availability_status: str = "Available"
    formulation_verified: bool = True
    strength_verified: bool = True
    route_verified: bool = True
    consistency_verified: bool = True
    alternative_medication: Optional[str] = None
    pharmacist_notes: Optional[str] = None


class PharmacistClarificationRequest(BaseModel):
    case_id: str
    patient_id: Optional[int] = None
    patient_name: Optional[str] = None
    medication: str
    reason: str
    notes: Optional[str] = None
    alternative_suggested: Optional[str] = None


# =========================================================================
# DOCTOR DASHBOARD ENDPOINT (WITH PHARMACY SUBSTITUTION REQUESTS)
# =========================================================================

@router.get("/api/dashboard/doctor", summary="Doctor Dashboard: Clinical findings, pending cosigns, and substitution requests")
def get_doctor_dashboard_data(
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: User = Depends(require_role(["Doctor"])),
):
    """Provides clinical decision support metrics, pending cosigns, patient risks, and pharmacy substitution requests for Physicians."""
    patients = db.execute(select(Patient).order_by(Patient.id)).scalars().all()
    patient_summaries = []
    for p in patients:
        meds = db.execute(select(Medication).where(Medication.patient_id == p.id)).scalars().all()
        labs = db.execute(select(Lab).where(Lab.patient_id == p.id).order_by(desc(Lab.id))).scalars().all()
        findings = db.execute(select(Finding).where(Finding.patient_id == p.id)).scalars().all()
        patient_summaries.append({
            "id": p.id,
            "patient_identifier": p.patient_identifier,
            "name": p.name,
            "sex": p.sex,
            "allergies": p.allergy_information or "Not recorded",
            "active_medications": [{"name": m.drug_name, "dose": f"{m.dose or ''} {m.dose_unit or ''}".strip(), "frequency": m.frequency} for m in meds],
            "recent_labs": [{"test": l.test_name, "value": l.value, "unit": l.unit or ""} for l in labs[:3]],
            "active_findings_count": len(findings),
        })

    # Retrieve simulated orders and co-signs from audit ledger
    all_events = audit_ledger.get_events()
    sim_events = [e for e in all_events if e.event_type == "SIMULATED_ORDER_CREATED"]
    cosigned_events = [e for e in all_events if e.event_type == "SIMULATED_ORDER_COSIGNED"]
    cosigned_map = {e.payload.get("simulation_id"): e.payload for e in cosigned_events if isinstance(e.payload, dict)}

    pending_cosigns = []
    for ev in sim_events:
        p = ev.payload if isinstance(ev.payload, dict) else {}
        sim_id = p.get("simulation_id")
        is_cosigned = sim_id in cosigned_map
        if not is_cosigned:
            pending_cosigns.append({
                "simulation_id": sim_id,
                "finding_id": p.get("finding_id"),
                "patient_id": p.get("patient_id"),
                "drug_name": p.get("drug_name"),
                "proposed_action": p.get("proposed_action"),
                "action_type": p.get("action_type"),
                "rationale": p.get("rationale"),
                "severity": p.get("severity", "High"),
                "source": p.get("source"),
                "evidence_id": p.get("evidence_id"),
                "requires_cosign": True,
                "status": "pending_cosign",
            })

    # The request table is the workflow source of truth; the audit ledger remains audit-only.
    substitution_rows = db.execute(
        select(SubstitutionRequest)
        .order_by(
            case((SubstitutionRequest.status == "pending", 0), else_=1),
            desc(SubstitutionRequest.created_at),
        )
        .limit(100)
    ).scalars().all()
    pharmacy_substitution_requests = [
        _substitution_request_response(db, request) for request in substitution_rows
    ]
    pending_substitution_count = sum(1 for request in substitution_rows if request.status == "pending")

    # Retrieve active findings
    findings = db.execute(select(Finding).order_by(desc(Finding.id)).limit(15)).scalars().all()
    findings_list = []
    for f in findings:
        trace_dict = f.trace if isinstance(f.trace, dict) else {}
        inputs_dict = f.inputs if isinstance(f.inputs, dict) else {}
        findings_list.append({
            "id": f.id,
            "patient_id": f.patient_id,
            "rule_id": f.rule_id,
            "severity": f.severity,
            "title": f.title,
            "description": f.description,
            "action": f.action,
            "source": trace_dict.get("source"),
            "evidence_id": trace_dict.get("evidence_id"),
            "source_version": trace_dict.get("source_version"),
            "inputs": inputs_dict,
            "created_at": f.created_at.strftime("%Y-%m-%d %H:%M:%S") if f.created_at else None,
        })

    recent_events = [
        {
            "id": e.id,
            "timestamp": e.timestamp,
            "actor": e.actor,
            "event_type": e.event_type,
            "summary": f"{e.event_type} by {e.actor}",
            "hash": e.hash[:16] + "...",
        }
        for e in all_events[-8:]
    ]

    return {
        "role": "Doctor",
        "clinician": current_user.full_name,
        "stats": {
            "total_patients": len(patients),
            "pending_cosigns_count": len(pending_cosigns),
            "substitution_requests_count": pending_substitution_count,
            "active_findings_count": len(findings_list),
            "critical_alerts_count": sum(1 for f in findings_list if f["severity"].lower() in ("critical", "high", "review_required")),
        },
        "patients": patient_summaries,
        "pending_cosigns": pending_cosigns,
        "substitution_requests": pharmacy_substitution_requests,
        "findings": findings_list,
        "recent_events": recent_events,
    }


# =========================================================================
# NURSE DASHBOARD: MEDICATION ADMINISTRATION RECORD (MAR) & VITALS INGESTION
# =========================================================================

@router.get("/api/dashboard/nurse", summary="Nurse Dashboard: persisted medications and labs")
def get_nurse_dashboard_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["Nurse", "Doctor", "Administrator"])),
):
    """Return persisted medication and laboratory records; the schema has no MAR schedule."""
    patients = db.execute(select(Patient).order_by(Patient.id)).scalars().all()
    medications = db.execute(select(Medication).order_by(Medication.patient_id, Medication.id)).scalars().all()
    labs = db.execute(select(Lab).order_by(Lab.patient_id, Lab.id)).scalars().all()

    high_attention_findings = db.execute(
        select(Finding).where(Finding.severity.in_(["critical", "high", "major"]))
    ).scalars().all()
    events = db.execute(
        select(Event).where(Event.event_type.in_(["MEDICATION_ADMINISTERED", "MEDICATION_HELD"]))
        .order_by(desc(Event.created_at), desc(Event.id))
    ).scalars().all()
    latest_mar_event = {}
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        medication_id = payload.get("medication_id")
        if isinstance(medication_id, int):
            latest_mar_event.setdefault(int(medication_id), event)

    patients_by_id = {patient.id: patient for patient in patients}
    mar_rows = []
    for medication in medications:
        patient = patients_by_id.get(medication.patient_id)
        event = latest_mar_event.get(medication.id)
        payload = event.payload if event and isinstance(event.payload, dict) else {}
        mar_rows.append({
            "id": f"MED-{medication.id}",
            "mar_id": f"MED-{medication.id}",
            "case_id": patient.patient_identifier if patient else None,
            "patient_id": patient.id if patient else medication.patient_id,
            "patient_identifier": patient.patient_identifier if patient else None,
            "patient_name": patient.name if patient else None,
            "medication_id": medication.id,
            "medication": medication.drug_name,
            "dose": " ".join(part for part in (medication.dose, medication.dose_unit) if part),
            "route": medication.route,
            "scheduled_time": None,
            "nursing_note": "No administration schedule is stored in the database.",
            "status": payload.get("status", "NOT_RECORDED"),
            "recorded_at": event.created_at.isoformat() if event else None,
            "recorded_by": event.payload.get("recorded_by") if event and isinstance(event.payload, dict) else None,
            "prescribing_doctor": None,
            "clinical_alert": None,
        })

    lab_rows = []
    for lab in labs:
        patient = patients_by_id.get(lab.patient_id)
        lab_rows.append({
            "id": lab.id,
            "patient_id": patient.id if patient else lab.patient_id,
            "patient_identifier": patient.patient_identifier if patient else None,
            "patient_name": patient.name if patient else None,
            "parameter": lab.test_name,
            "value": lab.value,
            "unit": lab.unit,
            "reference_range": lab.reference_range,
            "recorded_time": lab.measured_at.isoformat() if lab.measured_at else None,
            "status": "RECORDED",
            "source_type": "Persisted laboratory record",
        })

    return {
        "role": "Nurse",
        "nurse_name": current_user.full_name,
        "title": "Medication Administration Record & Inpatient Monitoring",
        "stats": {
            "medications_due": 0,
            "medications_administered": sum(1 for row in mar_rows if row["status"] == "ADMINISTERED"),
            "medications_held": sum(1 for row in mar_rows if row["status"] == "HELD"),
            "vitals_recorded": len(lab_rows),
            "attention_required": len(high_attention_findings),
            "assigned_patients_count": len(patients),
        },
        "mar_schedule": mar_rows,
        "vitals_labs": lab_rows,
        "patients": [
            {"patient_identifier": patient.patient_identifier, "name": patient.name}
            for patient in patients
        ],
        "mar_schedule_available": False,
        "mar_schedule_note": "No administration schedule is stored in the current database schema.",
    }


class NurseMARActionRequest(BaseModel):
    mar_id: str
    action: str  # "ADMINISTER" / "ADMINISTERED" or "HOLD" / "HELD"
    case_id: Optional[str] = None
    patient_name: Optional[str] = None
    medication: Optional[str] = None
    dose: Optional[str] = None
    route: Optional[str] = None
    nurse_name: Optional[str] = None
    notes: Optional[str] = None
    hold_reason: Optional[str] = None


@router.post("/api/dashboard/nurse/mar-action", summary="Persist medication administration or hold")
def record_nurse_mar_action(
    req: NurseMARActionRequest,
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: User = Depends(require_role(["Nurse"])),
):
    action_type = req.action.strip().upper()
    if action_type not in {"ADMINISTER", "ADMINISTERED", "HOLD", "HELD"}:
        raise HTTPException(status_code=422, detail="Unsupported medication record action.")
    try:
        medication_id = int(req.mar_id.removeprefix("MED-"))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Medication record not found.") from exc
    medication = db.get(Medication, medication_id)
    if medication is None or (medication.status or "").lower() not in {"active", "ordered", "pending"}:
        raise HTTPException(status_code=404, detail="Active medication record not found.")
    patient = db.get(Patient, medication.patient_id)
    if patient is None:
        raise HTTPException(status_code=409, detail="Medication patient record is unavailable.")
    existing = db.execute(
        select(Event).where(Event.event_type.in_(["MEDICATION_ADMINISTERED", "MEDICATION_HELD"]))
        .order_by(desc(Event.created_at), desc(Event.id))
    ).scalars().all()
    for event in existing:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("medication_id") == medication.id and payload.get("status") == "ADMINISTERED":
            raise HTTPException(status_code=409, detail="Medication is already recorded as administered.")
    status_text = "ADMINISTERED" if action_type in {"ADMINISTER", "ADMINISTERED"} else "HELD"
    event_type = "MEDICATION_ADMINISTERED" if status_text == "ADMINISTERED" else "MEDICATION_HELD"
    payload = {
        "medication_id": medication.id,
        "patient_identifier": patient.patient_identifier,
        "medication": medication.drug_name,
        "status": status_text,
        "recorded_by": current_user.full_name,
        "notes": req.notes,
        "hold_reason": req.hold_reason,
    }
    stored = Event(patient_id=patient.id, event_type=event_type, payload=payload)
    db.add(stored)
    db.commit()
    db.refresh(stored)
    audit_record = audit_ledger.append_event(
        actor=f"{current_user.full_name} (Nurse)", event_type=event_type, payload={**payload, "event_id": stored.id}
    )
    return {
        "status": status_text,
        "event_type": event_type,
        "mar_id": f"MED-{medication.id}",
        "audit_hash": audit_record.hash,
        "message": "Medication status event persisted.",
    }


class NurseRecordVitalRequest(BaseModel):
    case_id: str
    patient_name: Optional[str] = None
    parameter: str = Field(min_length=1, max_length=255)
    value: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=50)
    nurse_name: Optional[str] = None
    notes: Optional[str] = None


@router.post("/api/dashboard/nurse/record-vital", summary="Persist a laboratory or bedside measurement")
def record_nurse_vital(
    req: NurseRecordVitalRequest,
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["Nurse"])),
):
    patient = _find_history_patient(db, req.case_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    lab = Lab(
        patient_id=patient.id,
        test_name=req.parameter.strip(),
        value=req.value.strip(),
        unit=req.unit.strip(),
        measured_at=datetime.utcnow(),
    )
    db.add(lab)
    db.commit()
    db.refresh(lab)
    payload = {
        "lab_id": lab.id,
        "patient_identifier": patient.patient_identifier,
        "parameter": lab.test_name,
        "value": lab.value,
        "unit": lab.unit,
        "recorded_by": current_user.full_name,
    }
    audit_record = audit_ledger.append_event(
        actor=f"{current_user.full_name} (Nurse)", event_type="VITAL_SIGN_RECORDED", payload=payload
    )
    entry = {
        "id": lab.id,
        "case_id": patient.patient_identifier,
        "patient_identifier": patient.patient_identifier,
        "patient_name": patient.name,
        "parameter": lab.test_name,
        "value": lab.value,
        "unit": lab.unit,
        "recorded_time": lab.measured_at.isoformat(),
        "status": "RECORDED",
        "source_type": "Persisted laboratory record",
        "recorded_by": current_user.full_name,
    }
    return {
        "status": "RECORDED",
        "record": entry,
        "vital_entry": entry,
        "audit_hash": audit_record.hash,
        "message": "Measurement persisted.",
    }


# =========================================================================
# PHARMACIST WORKFLOW: MEDICATION FULFILLMENT & SUBSTITUTION
# =========================================================================

@router.get("/api/dashboard/pharmacist", summary="Pharmacist Dashboard: persisted prescriptions")
def get_pharmacist_dashboard_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["Clinical Pharmacist"])),
):
    """Return persisted orders and substitution requests. Inventory is not modeled in this database."""
    orders = db.execute(select(Order).order_by(desc(Order.ordered_at), desc(Order.id))).scalars().all()
    order_ids = [order.id for order in orders]
    requests_query = select(SubstitutionRequest).where(SubstitutionRequest.order_id.in_(order_ids))
    if getattr(current_user, "id", None) is not None:
        requests_query = requests_query.where(SubstitutionRequest.requested_by_user_id == current_user.id)
    requests = db.execute(
        requests_query.order_by(desc(SubstitutionRequest.created_at), desc(SubstitutionRequest.id))
    ).scalars().all() if order_ids else []
    latest_request = {}
    for request in requests:
        latest_request.setdefault(request.order_id, request)

    queue = []
    for order in orders:
        patient = db.get(Patient, order.patient_id)
        medication = db.get(Medication, order.medication_id) if order.medication_id else None
        request = latest_request.get(order.id)
        request_status = request.status if request else None
        if request_status == "pending":
            status_text = "Pending Doctor Verification"
        elif request_status == "approved":
            status_text = "Substitution Approved (Ready to Dispense)"
        elif request_status == "rejected":
            status_text = "Substitution Rejected by Doctor"
        elif request_status == "clarification_required":
            status_text = "Clarification Required"
        else:
            status_text = order.status or "Status not recorded"

        dose = " ".join(part for part in (order.dose, order.dose_unit) if part)
        queue.append({
            "case_id": patient.patient_identifier if patient else "",
            "order_id": order.id,
            "medication_id": medication.id if medication else None,
            "patient_id": patient.id if patient else order.patient_id,
            "patient_name": patient.name if patient else "",
            "patient_identifier": patient.patient_identifier if patient else None,
            "prescribed_medicine": order.drug_name,
            "medication": order.drug_name,
            "prescribed_brand": None,
            "mrn": None,
            "prescribing_doctor": None,
            "generic_name": medication.drug_name if medication else order.drug_name,
            "strength": dose,
            "formulation": "Not recorded",
            "route": order.route,
            "frequency": order.frequency,
            "availability": "UNTRACKED",
            "available_quantity": None,
            "available_qty_formatted": "Inventory is not recorded",
            "substitution_status": status_text,
            "substitution_request_id": request.id if request else None,
            "substitution_request_status": request_status,
            "suggested_brand": request.suggested_brand if request else None,
            "status": status_text,
            "is_ready_for_dispensing": False,
            "ordered_at": order.ordered_at.isoformat() if order.ordered_at else None,
        })

    findings = db.execute(select(Finding).order_by(desc(Finding.created_at), desc(Finding.id))).scalars().all()
    reference = [{
        "pair": finding.title,
        "mechanism": finding.description,
        "clinical_impact": finding.severity,
        "recommended_action": finding.action,
        "evidence_source": (finding.trace or {}).get("source") if isinstance(finding.trace, dict) else None,
        "evidence_id": (finding.trace or {}).get("evidence_id") if isinstance(finding.trace, dict) else None,
        "provenance": (finding.trace or {}).get("source_version") if isinstance(finding.trace, dict) else None,
        "finding_id": finding.id,
        "patient_id": finding.patient_id,
        "title": finding.title,
        "severity": finding.severity,
        "description": finding.description,
        "action": finding.action,
        "created_at": finding.created_at.isoformat() if finding.created_at else None,
    } for finding in findings]
    pending = sum(1 for row in queue if row["substitution_request_status"] == "pending")
    return {
        "role": "Clinical Pharmacist",
        "title": "Medication Fulfillment & Substitution",
        "primary_purpose": "Medication Availability & Brand Substitution Verification",
        "pharmacist_name": current_user.full_name,
        "stats": {
            "prescriptions_received": len(queue),
            "available": 0,
            "unavailable": 0,
            "substitution_requests": sum(1 for row in queue if row["substitution_request_id"] is not None),
            "doctor_approval_pending": pending,
            "ready_for_dispensing": 0,
            "pending_verification": len(queue) - pending,
            "ready_to_dispense": 0,
            "availability_issues": 0,
            "clarification_required": sum(1 for row in queue if row["substitution_request_status"] == "clarification_required"),
            "verified_today": 0,
            "inventory_tracked": False,
        },
        "availability_queue": queue,
        "verification_queue": queue,
        "local_inventory": [],
        "inventory_available": False,
        "pharmacology_insights": reference,
        "pharmacology_reference": reference,
        "active_rules": [],
    }


# =========================================================================
# PHARMACIST SUBSTITUTION REQUEST API
# =========================================================================

@router.post("/api/dashboard/pharmacist/substitution-request", summary="Pharmacist: Send brand substitution request to Doctor")
def create_substitution_request(
    req: PharmacistSubstitutionRequest,
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: User = Depends(require_role(["Clinical Pharmacist"])),
):
    """
    Persist a pharmacist request against an existing patient, order, and medication.
    Submission never changes the prescription or marks it ready for dispensing.
    """
    return submit_substitution_request(db, audit_ledger, current_user, req)


# =========================================================================
# DOCTOR SUBSTITUTION DECISION API (APPROVE / REJECT / CLARIFICATION)
# =========================================================================

@router.post("/api/dashboard/doctor/substitution-decision", summary="Doctor: Approve, Reject or Clarify Pharmacy Substitution Request")
def process_doctor_substitution_decision(
    req: DoctorSubstitutionDecisionRequest,
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: User = Depends(require_role(["Doctor"])),
):
    """
    Only a Doctor may decide a persisted request, and only while it is pending.
    """
    return decide_substitution_request(db, audit_ledger, current_user, req)


# =========================================================================
# PHARMACIST DISPENSE / VERIFICATION API
# =========================================================================

@router.post("/api/dashboard/pharmacist/dispense", summary="Verify persisted order inventory status")
@router.post("/api/dashboard/pharmacist/verify", summary="Verify persisted order inventory status")
def mark_ready_for_dispensing(
    req: PharmacistVerificationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["Clinical Pharmacist"])),
):
    """Reject dispensing verification because the current schema has no inventory source."""
    patient = _find_history_patient(db, req.case_id)
    if patient is None or (req.patient_id is not None and req.patient_id != patient.id):
        raise HTTPException(status_code=404, detail="Patient record not found.")
    orders = db.execute(select(Order).where(Order.patient_id == patient.id)).scalars().all()
    normalized = _normalize_medication_name(req.medication)
    matches = [order for order in orders if normalized and normalized in _normalize_medication_name(order.drug_name)]
    if len(matches) != 1:
        raise HTTPException(status_code=404 if not matches else 409, detail="Order reference is missing or ambiguous.")
    raise HTTPException(
        status_code=409,
        detail="Inventory is not tracked in the database; the order was not marked ready for dispensing.",
    )


@router.post("/api/dashboard/pharmacist/clarification", summary="Persist a pharmacist clarification request")
def request_doctor_clarification(
    req: PharmacistClarificationRequest,
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: User = Depends(require_role(["Clinical Pharmacist"])),
):
    patient = _find_history_patient(db, req.case_id)
    if patient is None or (req.patient_id is not None and req.patient_id != patient.id):
        raise HTTPException(status_code=404, detail="Patient record not found.")
    orders = db.execute(select(Order).where(Order.patient_id == patient.id)).scalars().all()
    normalized = _normalize_medication_name(req.medication)
    matches = [order for order in orders if normalized and normalized in _normalize_medication_name(order.drug_name)]
    if len(matches) != 1:
        raise HTTPException(status_code=404 if not matches else 409, detail="Order reference is missing or ambiguous.")
    payload = {
        "order_id": matches[0].id,
        "medication": matches[0].drug_name,
        "reason": req.reason,
        "notes": req.notes,
        "alternative_suggested": req.alternative_suggested,
        "requested_by": current_user.full_name,
    }
    event = Event(patient_id=patient.id, event_type="PHARMACIST_CLARIFICATION_REQUESTED", payload=payload)
    db.add(event)
    db.commit()
    db.refresh(event)
    audit_record = audit_ledger.append_event(
        actor=f"{current_user.full_name} (Clinical Pharmacist)",
        event_type="PHARMACIST_CLARIFICATION_REQUESTED",
        payload={**payload, "patient_identifier": patient.patient_identifier, "event_id": event.id},
    )
    return {
        "status": "AWAITING_DOCTOR_CLARIFICATION",
        "message": "Clarification request persisted for clinician review.",
        "event_id": event.id,
        "audit_hash": audit_record.hash,
    }


# COMPLETE PATIENT MEDICAL HISTORY API ENDPOINTS
# =========================================================================

def _patient_history_record(db: Session, patient: Patient) -> Dict[str, Any]:
    """Build a patient history exclusively from persisted records for that patient."""
    medications = db.execute(
        select(Medication).where(Medication.patient_id == patient.id).order_by(Medication.id)
    ).scalars().all()
    labs = db.execute(
        select(Lab).where(Lab.patient_id == patient.id).order_by(desc(Lab.measured_at), desc(Lab.id))
    ).scalars().all()
    orders = db.execute(
        select(Order).where(Order.patient_id == patient.id).order_by(desc(Order.ordered_at), desc(Order.id))
    ).scalars().all()
    findings = db.execute(
        select(Finding).where(Finding.patient_id == patient.id).order_by(desc(Finding.created_at), desc(Finding.id))
    ).scalars().all()
    events = db.execute(
        select(Event).where(Event.patient_id == patient.id).order_by(desc(Event.created_at), desc(Event.id))
    ).scalars().all()
    requests = db.execute(
        select(SubstitutionRequest).where(SubstitutionRequest.patient_id == patient.id)
        .order_by(desc(SubstitutionRequest.created_at), desc(SubstitutionRequest.id))
    ).scalars().all()

    age = None
    if patient.date_of_birth:
        today = datetime.utcnow().date()
        age = today.year - patient.date_of_birth.year - ((today.month, today.day) < (patient.date_of_birth.month, patient.date_of_birth.day))
    allergy = (patient.allergy_information or "").strip()
    medication_rows = [{
        "medication": medication.drug_name,
        "strength": " ".join(part for part in (medication.dose, medication.dose_unit) if part),
        "route": medication.route,
        "start_date": medication.start_date.isoformat() if medication.start_date else None,
        "end_date": medication.end_date.isoformat() if medication.end_date else None,
        "status": medication.status or "Not recorded",
        "prescribing_doctor": None,
    } for medication in medications]
    lab_rows = [{
        "date": lab.measured_at.isoformat() if lab.measured_at else (lab.created_at.isoformat() if lab.created_at else None),
        "test": lab.test_name,
        "result": lab.value,
        "unit": lab.unit,
        "reference_range": lab.reference_range,
        "status": "Not assessed",
        "lab_id": lab.id,
    } for lab in labs]
    assessment_rows = [{
        "date": finding.created_at.isoformat() if finding.created_at else None,
        "assessment": finding.title,
        "risk_level": finding.severity,
        "recommendation": finding.action,
        "doctor": "Not recorded",
        "description": finding.description,
    } for finding in findings]
    pharmacy_rows = [{
        "medication": db.get(Order, request.order_id).drug_name if db.get(Order, request.order_id) else "Not recorded",
        "availability": "Not tracked",
        "substitution_request": f"{request.suggested_brand}: {request.reason}",
        "doctor_verification": request.status,
        "dispensing_status": "Not tracked",
        "date": request.created_at.isoformat() if request.created_at else None,
        "recorded_date": None,
        "order_id": request.order_id,
        "suggested_brand": request.suggested_brand,
        "status": request.status,
        "reason": request.reason,
        "decided_at": request.decided_at.isoformat() if request.decided_at else None,
    } for request in requests]
    timeline = []
    if patient.created_at:
        timeline.append({
            "date": patient.created_at.isoformat(),
            "event": f"Patient registered: {patient.name}",
            "actor": "Not recorded",
            "badge": "REGISTRATION",
        })
    for medication in medications:
        dose_str = f" ({medication.dose} {medication.dose_unit})".strip() if medication.dose else ""
        timeline.append({
            "date": medication.start_date.isoformat() if medication.start_date else (medication.created_at.isoformat() if getattr(medication, "created_at", None) else None),
            "event": f"Medication therapy recorded: {medication.drug_name}{dose_str}",
            "actor": "Not recorded",
            "badge": "MEDICATION",
        })
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        timeline.append({
            "date": event.created_at.isoformat() if event.created_at else None,
            "event": event.event_type.replace("_", " ").title(),
            "actor": payload.get("recorded_by") or payload.get("actor") or "Not recorded",
            "badge": event.event_type,
        })
    for lab in labs:
        timeline.append({"date": lab.measured_at.isoformat() if lab.measured_at else None, "event": f"{lab.test_name} recorded", "actor": "Not recorded", "badge": "LAB"})
    for finding in findings:
        timeline.append({"date": finding.created_at.isoformat() if finding.created_at else None, "event": finding.title, "actor": "Not recorded", "badge": "CLINICAL"})
    for order in orders:
        timeline.append({"date": order.ordered_at.isoformat() if order.ordered_at else None, "event": f"Prescription order: {order.drug_name} ({order.status or 'status not recorded'})", "actor": "Not recorded", "badge": "PRESCRIPTION"})
    timeline.sort(key=lambda row: row["date"] or "", reverse=True)

    return {
        "case_id": patient.patient_identifier,
        "patient_identifier": patient.patient_identifier,
        "patient_id": patient.id,
        "patient_name": patient.name,
        "name": patient.name,
        "mrn": None,
        "age": age,
        "sex": patient.sex,
        "blood_group": None,
        "allergies_summary": allergy or "Not recorded",
        "conditions_summary": "Not recorded",
        "last_updated": patient.created_at.isoformat() if patient.created_at else None,
        "timeline": timeline,
        "medical_conditions": [],
        "medication_history": medication_rows,
        "allergies": ([{"allergen": allergy, "reaction": "Not recorded", "severity": "Not recorded"}] if allergy else []),
        "laboratory_history": lab_rows,
        "clinical_assessments": assessment_rows,
        "pharmacy_events": pharmacy_rows,
        "audit_events": [{
            "timestamp": event.created_at.isoformat() if event.created_at else None,
            "user_role": (event.payload or {}).get("recorded_by", "Not recorded") if isinstance(event.payload, dict) else "Not recorded",
            "action": event.event_type.replace("_", " ").title(),
            "event_type": event.event_type,
            "audit_hash": None,
            "event_id": event.id,
        } for event in events],
    }


def _find_history_patient(db: Session, identifier: str) -> Optional[Patient]:
    value = str(identifier).strip()
    if value.isdigit():
        return db.get(Patient, int(value))
    return db.execute(select(Patient).where(Patient.patient_identifier == value)).scalar_one_or_none()


@router.get("/api/dashboard/patient-history/{identifier}", summary="Get persisted Patient Medical History")
@router.get("/api/patient-history/{identifier}", summary="Get persisted Patient Medical History")
def get_patient_medical_history_endpoint(
    identifier: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["Doctor", "Nurse", "Clinical Pharmacist", "Administrator"])),
):
    patient = _find_history_patient(db, identifier)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return _patient_history_record(db, patient)


@router.get("/api/patient-history", summary="List persisted Patient Medical Histories")
def list_patient_medical_histories(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["Doctor", "Nurse", "Clinical Pharmacist", "Administrator"])),
):
    patients = db.execute(select(Patient).order_by(Patient.id)).scalars().all()
    return [_patient_history_record(db, patient) for patient in patients]


# =========================================================================
# AUDIT LEDGER CATEGORIZATION & ADMIN ENDPOINT
# =========================================================================

def _categorize_audit_record(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Categorizes audit events and extracts structured fields strictly from backend records.
    Categories: AUTHENTICATION, CLINICAL EVENT, SAFETY, RESOLUTION, VERIFICATION, SUBSTITUTION, SYSTEM.
    """
    et = str(event_type).upper()
    cat = "SYSTEM"
    entity = "System"
    action = et.replace("_", " ").title()
    result = "RECORDED ✓"
    rule_id = str(payload.get("rule_id") or "")
    evidence_id = str(payload.get("evidence_id") or "")
    provenance = str(payload.get("source") or payload.get("provenance") or "Deterministic Safety Core")

    if "AUTH" in et or "OTP" in et or "TOTP" in et or "LOGIN" in et or "LOGOUT" in et:
        cat = "AUTHENTICATION"
        phone = payload.get("phone_number_masked") or payload.get("phone_number") or "User"
        role = payload.get("role") or "Clinician"
        entity = f"{phone} ({role})"
        if "LOGIN_SUCCESS" in et or "TOTP_SUCCESS" in et:
            action = "Secure Session Established"
            result = "SUCCESS ✓"
        elif "LOGOUT" in et:
            action = "Session Inactivated"
            result = "LOGGED OUT ✓"
        elif "FAILED" in et:
            action = str(payload.get("reason") or "Authentication Failed")
            result = "REJECTED ✗"
        elif "ENROLLED" in et:
            action = "RFC 6238 TOTP Key Activated"
            result = "ENROLLED ✓"
        elif "REQUESTED" in et:
            prov = payload.get("provider", "Local")
            action = f"Passcode Dispatched via {prov}"
            result = "DISPATCHED ✓"

    elif "PATIENT" in et or "MEDICATION" in et or "LAB" in et or "EVENT_INGESTED" in et:
        cat = "CLINICAL EVENT"
        p_id = payload.get("patient_id")
        case_id = payload.get("case_id")
        entity = f"Patient #{p_id}" if p_id else (str(case_id) if case_id else "Clinical EHR")
        action = str(payload.get("action_description") or payload.get("event_type") or "Clinical Event Ingested")
        result = "INGESTED ✓"

    elif "RISK" in et or "FINDING" in et or "SAFETY" in et:
        cat = "SAFETY"
        p_id = payload.get("patient_id")
        case_id = payload.get("case_id")
        entity = f"Patient #{p_id}" if p_id else (str(case_id) if case_id else "Safety Engine")
        title = payload.get("title") or "Risk Evaluated"
        sev = payload.get("severity") or "REVIEW REQUIRED"
        action = f"{title} [{str(sev).upper()}]"
        result = "FLAGGED ⚠️"

    elif "SUBSTITUTION" in et or "CLARIFICATION" in et:
        cat = "SUBSTITUTION"
        case = payload.get("case_id") or "Case"
        pt = payload.get("patient_name") or case
        entity = f"{pt} ({case})"
        if "REQUESTED" in et:
            sug = payload.get("suggested_brand") or "Alternative Brand"
            action = f"Pharmacy Substitution Requested: {sug}"
            result = "DOCTOR APPROVAL PENDING ⏳"
        elif "APPROVED" in et:
            sug = payload.get("suggested_brand") or "Alternative Brand"
            action = f"Doctor Approved Brand Substitution: {sug}"
            result = "SUBSTITUTION APPROVED ✓"
        elif "REJECTED" in et:
            action = f"Doctor Rejected Substitution: {payload.get('suggested_brand', 'Alternative')}"
            result = "SUBSTITUTION REJECTED ✗"
        elif "CLARIFICATION" in et:
            action = f"Clarification Requested: {payload.get('reason', 'Medication Review')}"
            result = "CLARIFICATION REQUIRED ⚠️"

    elif "PHARMACIST" in et or "DISPENS" in et or "PRESCRIPTION" in et:
        cat = "VERIFICATION"
        pharm = payload.get("pharmacist_name") or "Marcus Vance, PharmD"
        entity = f"{pharm} (Clinical Pharmacist)"
        med = payload.get("medication") or "Medication"
        case = payload.get("case_id") or "Case"
        if "VERIFIED" in et or "READY" in et:
            action = f"Prescription Verified & Approved for Dispensing: {med} ({case})"
            result = "READY TO DISPENSE ✓"
        elif "CLARIFICATION" in et:
            action = f"Clarification Requested: {med} - {payload.get('reason', 'Review Needed')}"
            result = "CLARIFICATION REQ ⚠️"
        else:
            action = f"Fulfillment Decision: {med}"
            result = "RECORDED ✓"

    elif "COSIGN" in et or "VERIF" in et or "REEVAL" in et:
        cat = "VERIFICATION"
        entity = str(payload.get("cosigned_by") or "Attending Physician")
        dec = str(payload.get("decision") or "Approved").upper()
        action = f"Simulated Order Decision: {dec}"
        result = f"{dec} ✓"

    return {
        "category": cat,
        "entity": entity,
        "action": action,
        "result": result,
        "rule_id": rule_id,
        "evidence_id": evidence_id,
        "provenance": provenance,
    }


@router.get("/api/dashboard/admin", summary="Admin Dashboard: System health, user sessions, audit chain, and SMS gateway")
def get_admin_dashboard_data(
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: User = Depends(require_role(["Administrator"])),
):
    """Provides system health, cryptographic audit chain verification, and SMS provider gateway controls."""
    verification = audit_ledger.verify_chain()
    all_events = audit_ledger.get_events()
    
    audit_records = []
    for e in reversed(all_events):
        p_dict = e.payload if isinstance(e.payload, dict) else {}
        extracted = _categorize_audit_record(e.event_type, p_dict)
        audit_records.append({
            "id": e.id,
            "timestamp": e.timestamp,
            "actor": e.actor,
            "event_type": e.event_type,
            "category": extracted["category"],
            "entity": extracted["entity"],
            "action": extracted["action"],
            "result": extracted["result"],
            "rule_id": extracted["rule_id"],
            "evidence_id": extracted["evidence_id"],
            "provenance": extracted["provenance"],
            "prev_hash": e.prev_hash,
            "prev_hash_short": e.prev_hash[:12] + "...",
            "hash": e.hash,
            "hash_short": e.hash[:12] + "...",
            "payload": p_dict,
            "is_chain_valid": verification.is_valid,
            "patient_identifier": p_dict.get("patient_identifier") or p_dict.get("case_id"),
        })

    users = db.execute(select(User).order_by(User.id)).scalars().all()
    sessions = db.execute(select(UserSession).where(UserSession.is_active == True).order_by(desc(UserSession.id)).limit(10)).scalars().all()
    patients = db.execute(
        select(Patient)
        .options(selectinload(Patient.medications), selectinload(Patient.findings))
        .order_by(Patient.id)
    ).scalars().all()
    patient_records = []
    severity_rank = {"CRITICAL": 4, "MAJOR": 3, "HIGH": 3, "MODERATE": 2, "MINOR": 1, "LOW": 1}
    for patient in patients:
        findings = sorted(patient.findings, key=lambda row: row.created_at or datetime.min, reverse=True)
        medications = [row for row in patient.medications if (row.status or "").lower() == "active"]
        risk = max(
            (str(row.severity or "").upper() for row in findings),
            key=lambda value: severity_rank.get(value, 0),
            default="NOT_RECORDED",
        )
        patient_records.append({
            "patient_identifier": patient.patient_identifier,
            "name": patient.name,
            "date_of_birth": patient.date_of_birth.isoformat() if patient.date_of_birth else None,
            "sex": patient.sex,
            "department": None,
            "latest_finding": findings[0].title if findings else None,
            "active_medications": [row.drug_name for row in medications],
            "risk": risk,
        })

    providers = list_available_providers()
    active_prov = get_sms_provider()

    return {
        "role": "Administrator",
        "admin_name": current_user.full_name,
        "system_health": {
            "status": "CONNECTED",
            "offline_mode": "Not reported",
            "zero_external_calls": "Not monitored",
            "database_engine": db.get_bind().dialect.name,
            "rule_engine": "Deterministic Priority Safety Engine",
            "ollama_local_status": "Not monitored",
        },
        "audit_chain": {
            "is_valid": verification.is_valid,
            "total_records": verification.total_records,
            "errors": verification.errors,
            "recent_records": audit_records,
        },
        "sms_gateway": {
            "active_provider": active_prov.get_provider_name(),
            "providers": providers,
        },
        "users_count": len(users),
        "active_sessions_count": len(sessions),
        "patients": patient_records,
    }
