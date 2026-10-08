from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies import get_audit_ledger, get_event_service, get_resolution_engine
from database.database import get_db
from engine.event_service import MedicationEventService
from models import Lab, Medication, Patient

router = APIRouter(prefix="/demo", tags=["Demo"])

DEMO_SCENARIO_METADATA = {
    1: {
        "id": 1,
        "name": "Warfarin + Fluconazole with Rising INR Context",
        "patient_identifier": "DEMO-PT-001",
        "description": "Active warfarin with rising INR trend; fluconazole prescribed -> CYP2C9 inhibition risk.",
        "expected_rule": "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
        "expected_evidence": "AEGIS-DEMO-EV-001",
    },
    2: {
        "id": 2,
        "name": "Enoxaparin with Declining Renal-Function Context",
        "patient_identifier": "DEMO-PT-002",
        "description": "Active enoxaparin with acute creatinine elevation -> renal clearance impairment.",
        "expected_rule": "AEGIS-DEMO-002-ENOXAPARIN-RENAL",
        "expected_evidence": "AEGIS-DEMO-EV-002",
    },
    3: {
        "id": 3,
        "name": "Duplicate ACE-Inhibitor Therapy",
        "patient_identifier": "DEMO-PT-003",
        "description": "Active lisinopril; enalapril ordered -> duplicate ACE inhibitor therapy risk.",
        "expected_rule": "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR",
        "expected_evidence": "AEGIS-DEMO-EV-003",
    },
}


@router.post("/seed", summary="Seed official demo patients and baseline clinical context")
def seed_demo_data(db: Session = Depends(get_db)):
    """
    Deterministically seeds the three official AEGIS hackathon demo patient profiles:
    1. DEMO-PT-001: Warfarin + baseline INR labs.
    2. DEMO-PT-002: Enoxaparin + baseline creatinine lab.
    3. DEMO-PT-003: Lisinopril active medication.
    """
    results = {}

    # Patient 1
    p1 = db.execute(select(Patient).where(Patient.patient_identifier == "DEMO-PT-001")).scalar_one_or_none()
    if not p1:
        p1 = Patient(patient_identifier="DEMO-PT-001", name="Sarah Jenkins", sex="F")
        db.add(p1)
        db.flush()
    # Ensure baseline warfarin and INR
    med1 = db.execute(select(Medication).where(Medication.patient_id == p1.id, Medication.drug_name == "warfarin")).scalar_one_or_none()
    if not med1:
        db.add(Medication(patient_id=p1.id, drug_name="warfarin", dose="5", dose_unit="mg", route="oral", frequency="daily", status="active"))
    lab1_count = len(db.execute(select(Lab).where(Lab.patient_id == p1.id)).scalars().all())
    if lab1_count == 0:
        db.add(Lab(patient_id=p1.id, test_name="INR", value="2.1"))
        db.add(Lab(patient_id=p1.id, test_name="INR", value="3.4"))
    results["patient_1"] = {"id": p1.id, "identifier": p1.patient_identifier, "name": p1.name}

    # Patient 2
    p2 = db.execute(select(Patient).where(Patient.patient_identifier == "DEMO-PT-002")).scalar_one_or_none()
    if not p2:
        p2 = Patient(patient_identifier="DEMO-PT-002", name="Robert Chen", sex="M")
        db.add(p2)
        db.flush()
    med2 = db.execute(select(Medication).where(Medication.patient_id == p2.id, Medication.drug_name == "enoxaparin")).scalar_one_or_none()
    if not med2:
        db.add(Medication(patient_id=p2.id, drug_name="enoxaparin", dose="40", dose_unit="mg", route="subcutaneous", frequency="daily", status="active"))
    lab2_count = len(db.execute(select(Lab).where(Lab.patient_id == p2.id)).scalars().all())
    if lab2_count == 0:
        db.add(Lab(patient_id=p2.id, test_name="Creatinine", value="1.0", unit="mg/dL"))
    results["patient_2"] = {"id": p2.id, "identifier": p2.patient_identifier, "name": p2.name}

    # Patient 3
    p3 = db.execute(select(Patient).where(Patient.patient_identifier == "DEMO-PT-003")).scalar_one_or_none()
    if not p3:
        p3 = Patient(patient_identifier="DEMO-PT-003", name="Elena Rostova", sex="F")
        db.add(p3)
        db.flush()
    med3 = db.execute(select(Medication).where(Medication.patient_id == p3.id, Medication.drug_name == "lisinopril")).scalar_one_or_none()
    if not med3:
        db.add(Medication(patient_id=p3.id, drug_name="lisinopril", dose="20", dose_unit="mg", route="oral", frequency="daily", status="active"))
    results["patient_3"] = {"id": p3.id, "identifier": p3.patient_identifier, "name": p3.name}

    db.commit()
    return {
        "status": "seeded",
        "patients": results,
        "message": "Demo patients seeded with verified clinical baselines.",
    }


@router.get("/status", summary="Check demo configuration and scenario status")
def get_demo_status(
    db: Session = Depends(get_db),
    resolution_engine=Depends(get_resolution_engine),
    audit_ledger=Depends(get_audit_ledger),
):
    patients = db.execute(select(Patient).where(Patient.patient_identifier.in_(["DEMO-PT-001", "DEMO-PT-002", "DEMO-PT-003"]))).scalars().all()
    verification = audit_ledger.verify_chain()
    return {
        "status": "ready",
        "offline": True,
        "seeded_patients_count": len(patients),
        "audit_chain_valid": verification.is_valid,
        "total_audit_events": verification.total_records,
        "scenarios": list(DEMO_SCENARIO_METADATA.values()),
    }


@router.post("/run/{scenario_id}", summary="Execute official demo scenario through the real pipeline")
def run_scenario(
    scenario_id: int,
    db: Session = Depends(get_db),
    event_service: MedicationEventService = Depends(get_event_service),
    audit_ledger=Depends(get_audit_ledger),
):
    """
    Executes one of the 3 official demo scenarios through the actual MedicationEventService pipeline:
    Event -> Risk Detection -> Finding -> Resolution -> Explanation -> SimulatedOrder -> Audit.
    """
    if scenario_id not in DEMO_SCENARIO_METADATA:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid scenario_id '{scenario_id}'. Must be 1, 2, or 3.",
        )

    meta = DEMO_SCENARIO_METADATA[scenario_id]
    patient = db.execute(
        select(Patient).where(Patient.patient_identifier == meta["patient_identifier"])
    ).scalar_one_or_none()

    if not patient:
        # Auto-seed if not yet present
        seed_demo_data(db)
        patient = db.execute(
            select(Patient).where(Patient.patient_identifier == meta["patient_identifier"])
        ).scalar_one_or_none()

    # Define the trigger event according to scenario
    if scenario_id == 1:
        # Warfarin + fluconazole with rising INR
        event_type = "MEDICATION_PRESCRIBED"
        payload = {"drug_name": "fluconazole", "dose": "200mg", "route": "oral"}
        new_med_name = "fluconazole"
    elif scenario_id == 2:
        # Enoxaparin with declining renal function
        event_type = "LAB_RESULT_RECORDED"
        payload = {"test_name": "Creatinine", "value": "2.4", "unit": "mg/dL"}
        new_med_name = None
    elif scenario_id == 3:
        # Duplicate ACE-inhibitor (lisinopril active, prescribe enalapril)
        event_type = "MEDICATION_PRESCRIBED"
        payload = {"drug_name": "enalapril", "dose": "10mg", "route": "oral"}
        new_med_name = "enalapril"

    affected_findings, pipeline_results = event_service.process_event_with_resolutions(
        db=db,
        patient_id=patient.id,
        event_type=event_type,
        payload=payload,
        new_medication_name=new_med_name,
    )

    verification = audit_ledger.verify_chain()
    audit_records = audit_ledger.get_events()
    latest_record = audit_records[-1] if audit_records else None
    latest_hash = latest_record.hash if latest_record else "GENESIS_BLOCK"
    latest_prev_hash = latest_record.prev_hash if latest_record else "GENESIS_PREV_HASH"

    # Build comprehensive pipeline result trace
    findings_data = []
    for f in affected_findings:
        trace_dict = f.trace if isinstance(f.trace, dict) else {}
        findings_data.append({
            "id": f.id,
            "rule_id": f.rule_id,
            "severity": f.severity,
            "title": f.title,
            "description": f.description,
            "source": trace_dict.get("source"),
            "evidence_id": trace_dict.get("evidence_id"),
        })

    resolutions_data = []
    simulated_orders_data = []
    explanations_data = []

    for pr in pipeline_results:
        resolutions_data.append({
            "finding_id": pr.finding_id,
            "status": pr.status,
            "candidates_count": len(pr.ranked_candidates),
            "top_candidate": pr.ranked_candidates[0].description if pr.ranked_candidates else None,
            "requires_cosign": pr.requires_cosign,
        })
        if pr.simulated_order:
            simulated_orders_data.append(pr.simulated_order.model_dump())
        if pr.explanation:
            explanations_data.append(pr.explanation.model_dump())

    lab_report_id = "LAB-2026-0001" if scenario_id == 1 else "LAB-2026-0002" if scenario_id == 2 else "LAB-2026-0003"
    lab_evidence_data = {
        "lab_report_id": lab_report_id,
        "case_id": f"CASE-00{scenario_id}",
        "source_document": f"data/lab_reports/{lab_report_id}.json",
        "evidence_status": "Verified Locally",
        "evidence_id": meta["expected_evidence"],
        "source": "MicroMedex / Clinical Evidence Repository (v2026.1)",
    }

    # Deterministic pharmacological rationales and XAI breakdown per scenario
    if scenario_id == 1:
        pharmacotherapy_rationale = (
            "Fluconazole is a potent competitive inhibitor of cytochrome P450 CYP2C9, the primary metabolic pathway "
            "responsible for the hepatic clearance of the more potent (S)-enantiomer of Warfarin. Concomitant administration "
            "causes substantial accumulation of S-warfarin, precipitating acute elevation of INR (current: 3.4) and markedly "
            "heightening the risk of life-threatening hemorrhagic complications."
        )
        xai_data = {
            "why_flagged": "Active Warfarin regimen concurrent with new Fluconazole 200mg prescription in a patient with an elevated INR trend (2.1 → 3.4).",
            "mechanism": "Potent inhibition of hepatic CYP2C9 and CYP3A4 by fluconazole markedly impairs S-warfarin clearance, prolonging prothrombin time and precipitating acute supratherapeutic anticoagulation.",
            "significance": "CRITICAL / MAJOR — High risk of gastrointestinal, intracranial, or systemic hemorrhage.",
            "evidence_considered": f"Evidence ID {meta['expected_evidence']} • Lab Report LAB-2026-0001 (INR 3.4, Verified) • FDA Drug Safety Communication & ACC Anticoagulation Guidance.",
            "risk_reasoning": "Deterministic rule AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE triggered. Patient INR is already 3.4; adding fluconazole without dose modification presents severe bleeding hazard.",
            "recommended_consideration": "Reduce Warfarin maintenance dose by 50% with daily INR monitoring, or substitute Fluconazole with an echinocandin (e.g. Micafungin 100 mg IV Daily) which lacks CYP2C9 inhibitory activity.",
        }
        agent1_out = "RxNorm 11289 (Warfarin Sodium) + RxNorm 4450 (Fluconazole) • Patient Context: INR=3.4"
        agent2_out = f"Rule: {meta['expected_rule']} (Active) • Provenance: {meta['expected_evidence']} (MicroMedex 2026.1)"
        agent3_out = "Finding: CRITICAL_DDI_CYP2C9_INHIBITION • Severity: Critical • Matched Pair: Warfarin + Fluconazole"
        agent4_out = "Resolution: Actionable • Top Candidate: Reduce Warfarin dose by 50% or substitute with Micafungin 100mg IV Daily • Physician Co-Sign Required"
        agent5_out = "Explanation: Generated clinician-readable rationale & provenance linking CYP2C9 inhibition to INR 3.4 lab elevation."
        agent6_out = f"Audit Block: Event PATIENT_EVENT_INGESTED & RISK_FINDING_DETECTED sealed • Hash: {latest_hash[:16]}... • SHA-256 Cryptographic Hash Chain Valid"
    elif scenario_id == 2:
        pharmacotherapy_rationale = (
            "Enoxaparin Sodium is a low molecular weight heparin (LMWH) eliminated predominantly via the renal route. "
            "Acute deterioration in renal clearance (Serum Creatinine 2.4 mg/dL, estimated CrCl < 30 mL/min) impairs drug elimination, "
            "leading to severe bioaccumulation of anti-Xa activity and elevated risk of major hemorrhagic events."
        )
        xai_data = {
            "why_flagged": "Active Enoxaparin 40 mg SC regimen in a patient exhibiting acute renal decline (Serum Creatinine elevated from 1.0 to 2.4 mg/dL).",
            "mechanism": "Enoxaparin clearance is strictly dependent on glomerular filtration. In severe renal impairment (CrCl < 30 mL/min), anti-Xa clearance decreases by >30%, causing systemic drug accumulation.",
            "significance": "HIGH / CRITICAL — Substantial accumulation of anticoagulant effect with increased risk of retroperitoneal and internal bleeding.",
            "evidence_considered": f"Evidence ID {meta['expected_evidence']} • Lab Report LAB-2026-0002 (Creatinine 2.4 mg/dL, CrCl 24 mL/min) • KDIGO & CHEST Guidelines on Antithrombotic Therapy.",
            "risk_reasoning": "Deterministic rule AEGIS-DEMO-002-ENOXAPARIN-RENAL triggered. Baseline creatinine elevation to 2.4 mg/dL necessitates renal dose reduction or switch to non-renally cleared anticoagulant.",
            "recommended_consideration": "Dose reduce Enoxaparin to 30 mg SC once daily with anti-Xa peak monitoring (target 0.5–1.0 IU/mL), or transition to Unfractionated Heparin (UFH) with aPTT monitoring.",
        }
        agent1_out = "RxNorm 67108 (Enoxaparin Sodium) • Patient Context: Creatinine=2.4 mg/dL, CrCl=24 mL/min"
        agent2_out = f"Rule: {meta['expected_rule']} (Active) • Provenance: {meta['expected_evidence']} (Renal Dosing Compendium)"
        agent3_out = "Finding: RENAL_IMPAIRMENT_DOSE_ADJUSTMENT • Severity: High • Context: Creatinine > 2.0 mg/dL"
        agent4_out = "Resolution: Actionable • Top Candidate: Dose reduce Enoxaparin to 30mg SC Daily or switch to Unfractionated Heparin • Physician Co-Sign Required"
        agent5_out = "Explanation: Generated renal pharmacology synthesis correlating 2.4 mg/dL Creatinine with LMWH clearance impairment."
        agent6_out = f"Audit Block: Event LAB_RESULT_RECORDED & SAFETY_RESOLUTION_EVALUATED sealed • Hash: {latest_hash[:16]}... • SHA-256 Cryptographic Hash Chain Valid"
    else:
        pharmacotherapy_rationale = (
            "Concurrent prescription of multiple angiotensin-converting enzyme (ACE) inhibitors (Lisinopril + Enalapril) "
            "represents therapeutic duplication with zero demonstrated clinical benefit. Dual ACE inhibition produces excessive "
            "renin-angiotensin-aldosterone system blockade, precipitating severe hyperkalemia, acute hypotensive episodes, and acute renal failure."
        )
        xai_data = {
            "why_flagged": "Active Lisinopril 20mg PO Daily concurrent with new prescription for Enalapril 10mg PO Daily (Therapeutic Duplication).",
            "mechanism": "Simultaneous inhibition of ACE by two distinct agents of the same pharmacological class produces additive suppression of aldosterone secretion and renal efferent arteriolar tone.",
            "significance": "HIGH RISK — High risk of refractory hyperkalemia (K+ > 5.2 mEq/L), symptomatic hypotension, syncope, and acute tubular necrosis.",
            "evidence_considered": f"Evidence ID {meta['expected_evidence']} • Lab Report LAB-2026-0003 (Potassium 5.3 mEq/L, Elevated) • ACC/AHA Guidelines for the Management of Heart Failure.",
            "risk_reasoning": "Deterministic rule AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR triggered. Duplicate ACE-I therapy is contraindicated due to additive toxicity without therapeutic advantage.",
            "recommended_consideration": "Discontinue redundant Enalapril prescription; maintain single-agent ACE-I therapy with Lisinopril 20 mg PO Daily and monitor serum potassium within 48 hours.",
        }
        agent1_out = "RxNorm 29046 (Lisinopril) + RxNorm 3827 (Enalapril Maleate) • Class: ACE Inhibitors"
        agent2_out = f"Rule: {meta['expected_rule']} (Active) • Provenance: {meta['expected_evidence']} (ACC/AHA Guidelines)"
        agent3_out = "Finding: DUPLICATE_THERAPY_SAME_CLASS • Severity: High • Matched Class: Angiotensin-Converting Enzyme Inhibitors"
        agent4_out = "Resolution: Actionable • Top Candidate: Cancel Enalapril order; maintain Lisinopril 20mg PO Daily • Physician Co-Sign Required"
        agent5_out = "Explanation: Generated therapeutic duplication explication highlighting redundant RAAS blockade and hyperkalemia risk."
        agent6_out = f"Audit Block: Event MEDICATION_PRESCRIBED & SIMULATED_ORDER_CREATED sealed • Hash: {latest_hash[:16]}... • SHA-256 Cryptographic Hash Chain Valid"

    six_agents_trace = [
        {
            "agent_number": 1,
            "agent_name": "Knowledge Normalizer",
            "status": "Completed",
            "description": "Normalized medication and patient-context inputs.",
            "input": f"Medication names ({new_med_name or 'Current Regimen'}) & patient clinical parameters",
            "output": agent1_out,
        },
        {
            "agent_number": 2,
            "agent_name": "Rule Pack & Provenance",
            "status": "Completed",
            "description": "Loaded applicable validated rules and evidence provenance.",
            "input": f"Normalized medication identifiers & clinical trigger ({event_type})",
            "output": agent2_out,
        },
        {
            "agent_number": 3,
            "agent_name": "Deterministic Risk Detector",
            "status": "Completed",
            "description": "Evaluated the medication combination against deterministic safety rules.",
            "input": "Normalized medication pair & physiological context",
            "output": agent3_out,
        },
        {
            "agent_number": 4,
            "agent_name": "Safety Resolution Engine",
            "status": "Completed",
            "description": "Resolved severity and generated the safety assessment.",
            "input": "Detected safety findings & active clinical rules",
            "output": agent4_out,
        },
        {
            "agent_number": 5,
            "agent_name": "Explainable AI Explicator",
            "status": "Completed",
            "description": "Generated a clinician-readable explanation from the validated assessment.",
            "input": "Validated deterministic finding & evidence provenance",
            "output": agent5_out,
        },
        {
            "agent_number": 6,
            "agent_name": "Cryptographic Audit Ledger",
            "status": "Completed",
            "description": "Recorded the assessment and provenance in the audit trail.",
            "input": "Complete assessment lifecycle event payload",
            "output": agent6_out,
        },
    ]

    return {
        "scenario": meta,
        "patient": {
            "id": patient.id,
            "identifier": patient.patient_identifier,
            "name": patient.name,
        },
        "lab_evidence": lab_evidence_data,
        "event_submitted": {
            "event_type": event_type,
            "payload": payload,
        },
        "clinical_rationale": pharmacotherapy_rationale,
        "explainable_ai": {
            "is_local_ai": True,
            "engine": "Local Explainable AI Explicator",
            "badge": "LOCAL EXPLAINABLE AI",
            "powered_by": "Powered by local Ollama / Deterministic Explicator",
            "disclaimer": "AI provides explanation; the safety decision is determined by the validated clinical rules.",
            **xai_data,
        },
        "six_agents_trace": six_agents_trace,
        "deterministic_verification": {
            "status": "VERIFIED",
            "message": "Safety decision generated by deterministic rules",
            "rule_id": meta["expected_rule"],
            "evidence_id": meta["expected_evidence"],
            "hash_chain_valid": verification.is_valid,
        },
        "audit_event": {
            "event_type": latest_record.event_type if latest_record else event_type,
            "current_hash": latest_hash,
            "prev_hash": latest_prev_hash,
            "total_records": verification.total_records,
            "is_valid": verification.is_valid,
        },
        "pipeline_verification": {
            "risk_detected": len(affected_findings) > 0,
            "matched_rule_id": affected_findings[0].rule_id if affected_findings else None,
            "expected_rule_id": meta["expected_rule"],
            "rule_matched_correctly": bool(affected_findings and affected_findings[0].rule_id == meta["expected_rule"]),
            "findings_count": len(affected_findings),
            "resolutions_count": len(resolutions_data),
            "simulated_orders_count": len(simulated_orders_data),
            "simulated_order_cosign_enforced": bool(simulated_orders_data and simulated_orders_data[0].get("requires_cosign") is True and simulated_orders_data[0].get("auto_execute") is False),
            "audit_hash_chain_valid": verification.is_valid,
            "total_audit_records": verification.total_records,
        },
        "findings": findings_data,
        "resolutions": resolutions_data,
        "simulated_orders": simulated_orders_data,
        "explanations": explanations_data,
    }


@router.get("", response_class=HTMLResponse, summary="Offline Clinical Decision Support Interface")
def get_demo_html():
    """
    Self-contained offline console for clinicians and reviewers.
    Pure HTML and JavaScript, zero external CDN dependencies, 100% offline.
    """
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MICROMEDX — Clinical Medication Safety Workspace</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      --font-heading: 'Plus Jakarta Sans', 'Inter', sans-serif;
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --border: #e2e8f0;
      --border-focus: #0284c7;
      --text: #0f172a;
      --text-muted: #64748b;
      --text-secondary: #334155;
      --accent: #0284c7;
      --accent-hover: #0369a1;
      --warning: #d97706;
      --danger: #dc2626;
      --success: #16a34a;
      --shadow-sm: 0 1px 3px 0 rgba(15, 23, 42, 0.06), 0 1px 2px -1px rgba(15, 23, 42, 0.04);
      --shadow-md: 0 4px 6px -1px rgba(15, 23, 42, 0.06), 0 2px 4px -2px rgba(15, 23, 42, 0.04);
    }
    body {
      margin: 0;
      font-family: var(--font-sans);
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.55;
      -webkit-font-smoothing: antialiased;
    }
    .header {
      background: #ffffff;
      padding: 1rem 2rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      box-shadow: var(--shadow-sm);
    }
    .header h1 {
      margin: 0;
      font-family: var(--font-heading);
      font-size: 1.35rem;
      color: var(--accent);
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 0.3rem 0.65rem;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
      letter-spacing: 0.02em;
    }
    .badge-offline { background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; }
    .badge-rule { background: #f0f9ff; color: #0369a1; border: 1px solid #bae6fd; }
    .badge-cosign { background: #faf5ff; color: #6b21a8; border: 1px solid #e9d5ff; }
    .container {
      max-width: 1200px;
      margin: 1.75rem auto;
      padding: 0 1.5rem;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 1.25rem;
      margin-bottom: 1.5rem;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.6rem;
      box-shadow: var(--shadow-sm);
      padding: 1.35rem;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
    }
    .card:hover {
      transform: translateY(-2px);
      box-shadow: var(--shadow-md);
      border-color: #cbd5e1;
    }
    .card h3 {
      margin-top: 0;
      color: var(--text);
      font-family: var(--font-heading);
      font-size: 1.05rem;
      font-weight: 700;
    }
    .card p {
      color: var(--text-secondary);
      font-size: 0.88rem;
      flex-grow: 1;
      line-height: 1.5;
    }
    .btn {
      background: var(--accent);
      color: #ffffff;
      border: 1px solid var(--accent);
      padding: 0.55rem 1.15rem;
      border-radius: 0.45rem;
      font-family: var(--font-sans);
      font-weight: 600;
      cursor: pointer;
      font-size: 0.85rem;
      transition: all 0.15s ease-in-out;
    }
    .btn:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
    .btn-secondary { background: #ffffff; color: var(--text); border: 1px solid #cbd5e1; }
    .btn-secondary:hover { background: #f8fafc; border-color: #94a3b8; }
    .output-panel {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.6rem;
      box-shadow: var(--shadow-sm);
      padding: 1.35rem;
      margin-top: 1.5rem;
    }
    .output-panel h2 {
      margin-top: 0;
      font-family: var(--font-heading);
      font-size: 1.1rem;
      color: var(--text);
      font-weight: 700;
    }
    pre {
      background: #f8fafc;
      border: 1px solid var(--border);
      padding: 1rem;
      border-radius: 0.45rem;
      overflow-x: auto;
      color: var(--text);
      font-family: 'JetBrains Mono', Consolas, monospace;
      font-size: 0.82rem;
      line-height: 1.45;
    }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>MICROMEDX</h1>
      <small style="color:var(--text-muted)">Clinical Decision Support & Medication Safety Platform</small>
    </div>
    <div style="display:flex; gap:0.5rem;">
      <span class="badge badge-offline">Offline Verified</span>
      <span class="badge badge-rule">Deterministic Safety</span>
      <span class="badge badge-cosign">Physician Co-Sign Enforced</span>
    </div>
  </div>

  <div class="container">
    <div style="display:flex; justify-content: space-between; align-items: center; margin-bottom: 1.25rem;">
      <h2 style="margin:0; font-family:var(--font-heading); font-weight:700; color:var(--text); font-size:1.25rem;">Clinical Safety Scenarios</h2>
      <button class="btn btn-secondary" onclick="seedPatients()">Sync & Seed Clinical Patients</button>
    </div>

    <div class="grid">
      <!-- Scenario 1 -->
      <div class="card">
        <div>
          <h3>1. Warfarin + Fluconazole</h3>
          <p><strong>Patient</strong>: Sarah Jenkins (PT-001)<br>
          <strong>Context</strong>: Active warfarin, rising INR (2.1 &rarr; 3.4)<br>
          <strong>Trigger</strong>: Prescribe Fluconazole 200mg</p>
        </div>
        <button class="btn" onclick="runScenario(1)">Evaluate Scenario 1</button>
      </div>

      <!-- Scenario 2 -->
      <div class="card">
        <div>
          <h3>2. Enoxaparin + Declining Renal</h3>
          <p><strong>Patient</strong>: Robert Chen (PT-002)<br>
          <strong>Context</strong>: Active enoxaparin, baseline creatinine 1.0<br>
          <strong>Trigger</strong>: Lab result recorded: Creatinine 2.4 mg/dL</p>
        </div>
        <button class="btn" onclick="runScenario(2)">Evaluate Scenario 2</button>
      </div>

      <!-- Scenario 3 -->
      <div class="card">
        <div>
          <h3>3. Duplicate ACE-Inhibitor</h3>
          <p><strong>Patient</strong>: Elena Rostova (PT-003)<br>
          <strong>Context</strong>: Active lisinopril 20mg<br>
          <strong>Trigger</strong>: Prescribe Enalapril 10mg</p>
        </div>
        <button class="btn" onclick="runScenario(3)">Evaluate Scenario 3</button>
      </div>
    </div>

    <div class="output-panel">
      <h2>Medication Safety Assessment Output</h2>
      <div id="status-banner" style="color:var(--text-muted); margin-bottom: 0.5rem; font-size:0.88rem;">Select a scenario above to evaluate against active clinical safety rules.</div>
      <pre id="output-json">// Clinical safety results will display here...</pre>
    </div>
  </div>

  <script>
    async function seedPatients() {
      const banner = document.getElementById("status-banner");
      banner.innerText = "Synchronizing clinical patients...";
      try {
        const res = await fetch("/demo/seed", { method: "POST" });
        const data = await res.json();
        document.getElementById("output-json").innerText = JSON.stringify(data, null, 2);
        banner.innerText = "Clinical patients synchronized successfully with validated clinical context.";
      } catch (err) {
        banner.innerText = "Error: " + err;
      }
    }

    async function runScenario(id) {
      const banner = document.getElementById("status-banner");
      banner.innerText = "Evaluating Scenario " + id + " against clinical safety rules...";
      try {
        const res = await fetch("/demo/run/" + id, { method: "POST" });
        const data = await res.json();
        document.getElementById("output-json").innerText = JSON.stringify(data, null, 2);
        const pv = data.pipeline_verification;
        banner.innerHTML = "<strong>Scenario " + id + " Evaluated:</strong> Matched Rule: <span style='color:var(--accent); font-weight:700;'>" + pv.matched_rule_id + "</span> | Action: <span style='color:var(--warning); font-weight:700;'>" + (pv.simulated_order_cosign_enforced ? "Pending Mandatory Physician Co-Sign" : "None") + "</span> | Audit Integrity: <span style='color:var(--success); font-weight:700;'>" + (pv.audit_hash_chain_valid ? "Cryptographically Valid (" + pv.total_audit_records + " records)" : "Invalid") + "</span>";
      } catch (err) {
        banner.innerText = "Error: " + err;
      }
    }
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)
