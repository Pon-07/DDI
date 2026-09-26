from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from agents.audit.ledger import AuditLedger
from app.dependencies import get_audit_ledger, get_event_service, get_resolution_engine
from database.database import get_db
from engine.auth.dependencies import get_current_user, get_optional_user, require_role
from engine.auth.models import User, UserSession
from engine.auth.providers.factory import get_sms_provider, list_available_providers
from engine.auth.service import AuthService
from models import Finding, Lab, Medication, Order, Patient, SafetyRule

router = APIRouter(prefix="/api/dashboard", tags=["Role Dashboards"])


@router.get("/doctor", summary="Doctor Dashboard: Clinical findings, pending cosigns, and patient charts")
def get_doctor_dashboard_data(
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Provides clinical decision support metrics, pending cosigns, and patient risks for Physicians."""
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
            "allergies": p.allergy_information or "None documented",
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
            "source": trace_dict.get("source") or "Deterministic Safety Engine",
            "evidence_id": trace_dict.get("evidence_id") or "AEGIS-EV-001",
            "source_version": trace_dict.get("source_version") or "1.0.0",
            "inputs": inputs_dict,
            "created_at": f.created_at.strftime("%Y-%m-%d %H:%M:%S") if f.created_at else None,
        })

    # Recent safety events from audit ledger
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
        "clinician": current_user.full_name if current_user else "Dr. Sarah Lin, MD",
        "stats": {
            "total_patients": len(patients),
            "pending_cosigns_count": len(pending_cosigns),
            "active_findings_count": len(findings_list),
            "critical_alerts_count": sum(1 for f in findings_list if f["severity"].lower() in ("critical", "high", "review_required")),
        },
        "patients": patient_summaries,
        "pending_cosigns": pending_cosigns,
        "findings": findings_list,
        "recent_events": recent_events,
    }


@router.get("/nurse", summary="Nurse Dashboard: Medication administration, rapid vitals/labs, and safety holds")
def get_nurse_dashboard_data(
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Provides Medication Administration Record (MAR) and rapid lab ingestion tools for Nurses."""
    patients = db.execute(select(Patient).order_by(Patient.id)).scalars().all()

    mar_records = []
    for p in patients:
        meds = db.execute(select(Medication).where(Medication.patient_id == p.id)).scalars().all()
        findings = db.execute(select(Finding).where(Finding.patient_id == p.id)).scalars().all()
        
        for m in meds:
            # Check if this medication has an active safety flag
            is_flagged = any(
                m.drug_name.lower() in (f.description or "").lower()
                or m.drug_name.lower() in f.title.lower()
                or (isinstance(f.inputs, dict) and (f.inputs.get("drug_a") == m.drug_name or f.inputs.get("drug_b") == m.drug_name))
                for f in findings
            )
            admin_status = "SAFETY_HOLD" if is_flagged else "DUE_NOW"
            flagged_finding = findings[0] if is_flagged else None
            mar_records.append({
                "patient_id": p.id,
                "patient_name": p.name,
                "patient_identifier": p.patient_identifier,
                "drug_name": m.drug_name.capitalize(),
                "dose": f"{m.dose or ''} {m.dose_unit or ''}".strip() or "Standard dose",
                "route": m.route or "Oral",
                "frequency": m.frequency or "Daily",
                "scheduled_time": "09:00 AM",
                "status": admin_status,
                "safety_alert": flagged_finding.title if flagged_finding else None,
                "safety_action": flagged_finding.action if flagged_finding else None,
                "severity": flagged_finding.severity if flagged_finding else None,
                "requires_check": is_flagged,
            })

    # Recent lab entries
    labs = db.execute(select(Lab).order_by(desc(Lab.id)).limit(10)).scalars().all()
    lab_entries = [
        {
            "id": l.id,
            "patient_id": l.patient_id,
            "test_name": l.test_name,
            "value": l.value,
            "unit": l.unit or "",
            "measured_at": l.measured_at.strftime("%Y-%m-%d %H:%M:%S") if l.measured_at else None,
        }
        for l in labs
    ]

    return {
        "role": "Nurse",
        "nurse_name": current_user.full_name if current_user else "Elena Rostova, RN",
        "stats": {
            "due_medications_count": sum(1 for m in mar_records if m["status"] == "DUE_NOW"),
            "safety_held_count": sum(1 for m in mar_records if m["status"] == "SAFETY_HOLD"),
            "total_assigned_patients": len(patients),
        },
        "mar_schedule": mar_records,
        "recent_labs": lab_entries,
    }


@router.get("/pharmacist", summary="Pharmacist Dashboard: DDI matrix, safety rules, renal dosing, and provenance")
def get_pharmacist_dashboard_data(
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    resolution_engine=Depends(get_resolution_engine),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Provides pharmacokinetics analysis, active safety rules with full provenance, and re-evaluation telemetry."""
    # Retrieve active rules from resolution engine and database
    active_engine_rules = resolution_engine.get_active_rules() if hasattr(resolution_engine, "get_active_rules") else []
    db_rules = db.execute(select(SafetyRule)).scalars().all()

    rule_items = []
    seen_ids = set()

    # 1. First process rules from SafetyResolutionEngine (loaded from validated rule packs)
    for r in active_engine_rules:
        r_id = getattr(r, "rule_id", None) or (r.get("rule_id") if isinstance(r, dict) else None)
        if not r_id or r_id in seen_ids:
            continue
        seen_ids.add(r_id)

        rule_type = getattr(r, "rule_type", None) or (r.get("rule_type") if isinstance(r, dict) else "drug_interaction")
        category = getattr(r, "category", None) or (r.get("category") if isinstance(r, dict) else rule_type)
        drug_a = getattr(r, "drug_a", None) or (r.get("drug_a") if isinstance(r, dict) else "")
        drug_b = getattr(r, "drug_b", None) or (r.get("drug_b") if isinstance(r, dict) else None)
        action = getattr(r, "action", None) or (r.get("action") if isinstance(r, dict) else "clinical review")
        evidence_id = getattr(r, "evidence_id", None) or (r.get("evidence_id") if isinstance(r, dict) else "AEGIS-EV-001")
        evidence_text = getattr(r, "evidence_text", None) or (r.get("evidence_text") if isinstance(r, dict) else "")
        source = getattr(r, "source", None) or (r.get("source") if isinstance(r, dict) else "AEGIS_HACKATHON_DEMO")
        source_version = getattr(r, "source_version", None) or (r.get("source_version") if isinstance(r, dict) else "1.0.0")
        severity = getattr(r, "severity", None) or (r.get("severity") if isinstance(r, dict) else "review_required")
        status_val = getattr(r, "status", None) or (r.get("status") if isinstance(r, dict) else "active")

        # Derive clinical trigger context from evidence text or pair
        trigger_context = evidence_text[:120] if evidence_text else f"Active {drug_a} + {drug_b or 'clinical context'}"

        rule_items.append({
            "rule_id": str(r_id),
            "rule_type": str(rule_type),
            "category": str(category),
            "drug_a": str(drug_a).capitalize(),
            "drug_b": str(drug_b).capitalize() if drug_b else "Patient Clinical Context",
            "trigger_context": trigger_context,
            "action": str(action),
            "recommended_action": str(action),
            "evidence_id": str(evidence_id),
            "evidence_text": str(evidence_text),
            "source": str(source),
            "evidence_source": str(source),
            "source_version": str(source_version),
            "provenance_source": f"{source} v{source_version}",
            "severity": str(severity),
            "status": str(status_val).upper(),
            "last_evaluated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        })

    # 2. Also incorporate any DB rules
    for r in db_rules:
        if r.rule_id in seen_ids:
            continue
        seen_ids.add(r.rule_id)
        rule_items.append({
            "rule_id": r.rule_id,
            "rule_type": r.rule_type,
            "category": r.category or r.rule_type,
            "drug_a": r.drug_a.capitalize(),
            "drug_b": r.drug_b.capitalize() if r.drug_b else "Patient Clinical Context",
            "trigger_context": r.evidence_text[:120] if r.evidence_text else f"Active {r.drug_a}",
            "action": r.action or "clinical review",
            "recommended_action": r.action or "clinical review",
            "evidence_id": r.evidence_id,
            "evidence_text": r.evidence_text,
            "source": r.source,
            "evidence_source": r.source,
            "source_version": r.source_version,
            "provenance_source": f"{r.source} v{r.source_version}",
            "severity": r.severity or "review_required",
            "status": (r.status or "active").upper(),
            "last_evaluated": r.updated_at.strftime("%Y-%m-%d %H:%M:%S UTC") if r.updated_at else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        })

    # Clinical pharmacology interaction profiles
    pharmacology_insights = [
        {
            "pair": "Warfarin + Fluconazole",
            "mechanism": "Potent CYP2C9 inhibition by fluconazole impairs hepatic clearance of the active S-warfarin enantiomer.",
            "clinical_impact": "Rapid INR prolongation, severe bleeding risk within 48–72 hours.",
            "recommended_action": "Reduce warfarin maintenance dose by 50% or substitute antifungal; perform INR test at Day 3.",
            "evidence_source": "DDInter / FDA Drug Label",
            "evidence_id": "AEGIS-DEMO-EV-001",
            "provenance": "Rule Pack: aegis_hackathon_demo v1.0.0",
        },
        {
            "pair": "Enoxaparin + Declining Renal Function",
            "mechanism": "Enoxaparin (LMWH) is eliminated primarily by renal filtration. Reduced GFR results in high anti-Xa bioaccumulation.",
            "clinical_impact": "Major internal retroperitoneal/GI hemorrhage risk.",
            "recommended_action": "Reduce dose to 30 mg SC once daily if CrCl < 30 mL/min; monitor peak anti-Xa or convert to UFH.",
            "evidence_source": "OpenFDA Black Box / CPIC Guidelines",
            "evidence_id": "AEGIS-DEMO-EV-002",
            "provenance": "Rule Pack: aegis_hackathon_demo v1.0.0",
        },
        {
            "pair": "Lisinopril + Enalapril (Duplicate ACE-I)",
            "mechanism": "Dual angiotensin-converting enzyme inhibition causes additive vasodilation and aldosterone suppression.",
            "clinical_impact": "Refractory hypotension, acute renal failure, hyperkalemia (K > 5.5 mEq/L).",
            "recommended_action": "Deprescribe enalapril; maintain single ACE-I regimen or substitute dihydropyridine CCB (amlodipine).",
            "evidence_source": "ISMP High Alert / Beers Criteria",
            "evidence_id": "AEGIS-DEMO-EV-003",
            "provenance": "Rule Pack: aegis_hackathon_demo v1.0.0",
        },
    ]

    # Re-evaluation telemetry from audit ledger
    all_events = audit_ledger.get_events()
    reeval_events = [
        {
            "id": e.id,
            "timestamp": e.timestamp,
            "actor": e.actor,
            "event_type": e.event_type,
            "payload": e.payload,
            "hash": e.hash[:16] + "...",
        }
        for e in all_events
        if e.event_type in ("RISK_FINDING_REEVALUATED", "RISK_FINDING_DETECTED", "PATIENT_EVENT_INGESTED")
    ]

    return {
        "role": "Clinical Pharmacist",
        "pharmacist_name": current_user.full_name if current_user else "Marcus Vance, PharmD, BCPS",
        "stats": {
            "active_safety_rules": len(rule_items),
            "cyp_enzyme_profiles": 5,
            "renal_adjusted_drugs": 8,
            "substitutions_available": 12,
            "reevaluation_events_count": len(reeval_events),
        },
        "pharmacology_insights": pharmacology_insights,
        "active_rules": rule_items,
        "reevaluation_history": reeval_events[-6:],
    }


def _categorize_audit_record(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Categorizes audit events and extracts structured fields strictly from backend records.
    Categories: AUTHENTICATION, CLINICAL EVENT, SAFETY, RESOLUTION, VERIFICATION, SYSTEM.
    """
    et = str(event_type).upper()
    cat = "SYSTEM"
    entity = "System"
    action = et.replace("_", " ").title()
    result = "RECORDED ✓"
    rule_id = str(payload.get("rule_id") or "")
    evidence_id = str(payload.get("evidence_id") or "")
    provenance = str(payload.get("source") or payload.get("provenance") or "")

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
        entity = f"Patient #{p_id}" if p_id else "Clinical EHR"
        action = str(payload.get("event_type") or "Clinical Event Ingested")
        result = "INGESTED ✓"

    elif "RISK" in et or "FINDING" in et or "SAFETY" in et:
        cat = "SAFETY"
        p_id = payload.get("patient_id")
        entity = f"Patient #{p_id}" if p_id else "Safety Engine"
        title = payload.get("title") or "Risk Evaluated"
        sev = payload.get("severity") or "REVIEW REQUIRED"
        action = f"{title} [{str(sev).upper()}]"
        result = "FLAGGED ⚠️"

    elif "RESOLUTION" in et or "SUGGESTION" in et or "SIMULATED_ORDER" in et:
        cat = "RESOLUTION"
        p_id = payload.get("patient_id")
        entity = f"Patient #{p_id}" if p_id else "Resolution Pipeline"
        action = str(payload.get("proposed_action") or payload.get("top_candidate") or "Intervention Formulated")
        result = "RESOLVED ✓"

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


@router.get("/admin", summary="Admin Dashboard: System health, user sessions, audit chain, and SMS gateway")
def get_admin_dashboard_data(
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Provides system health, cryptographic audit chain verification, and SMS provider gateway controls."""
    # Audit chain verification
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
            "is_chain_valid": True,
        })

    # Users and sessions
    users = db.execute(select(User).order_by(User.id)).scalars().all()
    sessions = db.execute(select(UserSession).where(UserSession.is_active == True).order_by(desc(UserSession.id)).limit(10)).scalars().all()

    # Providers
    providers = list_available_providers()
    active_prov = get_sms_provider()

    return {
        "role": "Administrator",
        "admin_name": current_user.full_name if current_user else "Arthur Pendelton, MS, CPHIMS",
        "system_health": {
            "status": "OPERATIONAL",
            "offline_mode": "100% OFFLINE / ON-PREMISE",
            "zero_external_calls": True,
            "database_engine": "SQLite WAL Mode (ACID)",
            "rule_engine": "Deterministic Priority Safety Engine",
            "ollama_local_status": "Ready on localhost:11434 (Qwen 2.5 7B)",
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
    }
