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

    return {
        "scenario": meta,
        "patient": {
            "id": patient.id,
            "identifier": patient.patient_identifier,
            "name": patient.name,
        },
        "event_submitted": {
            "event_type": event_type,
            "payload": payload,
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


@router.get("", response_class=HTMLResponse, summary="Offline Minimal Demo Interface")
def get_demo_html():
    """
    Self-contained offline demo console for clinicians and judges.
    Pure HTML and JavaScript, zero external CDN dependencies, 100% offline.
    """
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>AEGIS Rx — Offline Medication Safety Platform Demo</title>
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --warning: #f59e0b;
      --danger: #ef4444;
      --success: #10b981;
    }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }
    .header {
      padding: 1.5rem 2rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .header h1 {
      margin: 0;
      font-size: 1.5rem;
      color: var(--accent);
    }
    .badge {
      display: inline-block;
      padding: 0.25rem 0.6rem;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .badge-offline { background: #064e3b; color: var(--success); border: 1px solid var(--success); }
    .badge-rule { background: #1e3a8a; color: var(--accent); }
    .container {
      max-width: 1200px;
      margin: 2rem auto;
      padding: 0 1.5rem;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 1.5rem;
      margin-bottom: 2rem;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }
    .card h3 { margin-top: 0; color: #fff; font-size: 1.15rem; }
    .card p { color: var(--text-muted); font-size: 0.9rem; flex-grow: 1; }
    .btn {
      background: var(--accent);
      color: #0f172a;
      border: none;
      padding: 0.6rem 1.2rem;
      border-radius: 0.4rem;
      font-weight: 600;
      cursor: pointer;
      font-size: 0.9rem;
      transition: opacity 0.2s;
    }
    .btn:hover { opacity: 0.9; }
    .btn-secondary { background: #475569; color: #fff; }
    .output-panel {
      background: #020617;
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.5rem;
      margin-top: 2rem;
    }
    .output-panel h2 { margin-top: 0; font-size: 1.2rem; color: var(--accent); }
    pre {
      background: #090d16;
      border: 1px solid #1e293b;
      padding: 1rem;
      border-radius: 0.5rem;
      overflow-x: auto;
      color: #38bdf8;
      font-size: 0.85rem;
    }
    .pipeline-step {
      display: inline-block;
      padding: 0.4rem 0.8rem;
      margin: 0.2rem;
      border-radius: 0.3rem;
      background: #1e293b;
      font-size: 0.8rem;
      border-left: 3px solid var(--accent);
    }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>AEGIS Rx</h1>
      <small style="color:var(--text-muted)">Offline Deterministic Medication Safety Platform</small>
    </div>
    <div>
      <span class="badge badge-offline">Offline Verified</span>
      <span class="badge badge-rule">Zero External Calls</span>
      <span class="badge" style="background:#701a75;color:#f472b6;">Cosign Enforced</span>
    </div>
  </div>

  <div class="container">
    <div style="display:flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem;">
      <h2 style="margin:0;">Official Hackathon Demo Scenarios</h2>
      <button class="btn btn-secondary" onclick="seedPatients()">Reset & Seed Demo Patients</button>
    </div>

    <div class="grid">
      <!-- Scenario 1 -->
      <div class="card">
        <div>
          <h3>1. Warfarin + Fluconazole</h3>
          <p><strong>Patient</strong>: Sarah Jenkins (DEMO-PT-001)<br>
          <strong>Context</strong>: Active warfarin, rising INR (2.1 &rarr; 3.4)<br>
          <strong>Trigger</strong>: Prescribe Fluconazole 200mg</p>
        </div>
        <button class="btn" onclick="runScenario(1)">Execute Scenario 1</button>
      </div>

      <!-- Scenario 2 -->
      <div class="card">
        <div>
          <h3>2. Enoxaparin + Declining Renal</h3>
          <p><strong>Patient</strong>: Robert Chen (DEMO-PT-002)<br>
          <strong>Context</strong>: Active enoxaparin, baseline creatinine 1.0<br>
          <strong>Trigger</strong>: Lab result recorded: Creatinine 2.4 mg/dL</p>
        </div>
        <button class="btn" onclick="runScenario(2)">Execute Scenario 2</button>
      </div>

      <!-- Scenario 3 -->
      <div class="card">
        <div>
          <h3>3. Duplicate ACE-Inhibitor</h3>
          <p><strong>Patient</strong>: Elena Rostova (DEMO-PT-003)<br>
          <strong>Context</strong>: Active lisinopril 20mg<br>
          <strong>Trigger</strong>: Prescribe Enalapril 10mg</p>
        </div>
        <button class="btn" onclick="runScenario(3)">Execute Scenario 3</button>
      </div>
    </div>

    <div style="margin: 1.5rem 0;">
      <div class="pipeline-step">1. Event Ingested</div>
      <div class="pipeline-step">&rarr; 2. Deterministic Risk Detection</div>
      <div class="pipeline-step">&rarr; 3. Finding Created</div>
      <div class="pipeline-step">&rarr; 4. Safety Resolution</div>
      <div class="pipeline-step">&rarr; 5. Explanation Generated</div>
      <div class="pipeline-step">&rarr; 6. Simulated Order (Pending Cosign)</div>
      <div class="pipeline-step">&rarr; 7. SHA-256 Audit Trail Linked</div>
    </div>

    <div class="output-panel">
      <h2>Pipeline Execution Output</h2>
      <div id="status-banner" style="color:var(--text-muted); margin-bottom: 0.5rem;">Select a scenario above to execute through the live offline pipeline.</div>
      <pre id="output-json">// Pipeline output will display here...</pre>
    </div>
  </div>

  <script>
    async function seedPatients() {
      const banner = document.getElementById("status-banner");
      banner.innerText = "Seeding demo patients...";
      try {
        const res = await fetch("/demo/seed", { method: "POST" });
        const data = await res.json();
        document.getElementById("output-json").innerText = JSON.stringify(data, null, 2);
        banner.innerText = "Demo patients seeded successfully with validated clinical context.";
      } catch (err) {
        banner.innerText = "Error: " + err;
      }
    }

    async function runScenario(id) {
      const banner = document.getElementById("status-banner");
      banner.innerText = "Running Scenario " + id + " through full medication event pipeline...";
      try {
        const res = await fetch("/demo/run/" + id, { method: "POST" });
        const data = await res.json();
        document.getElementById("output-json").innerText = JSON.stringify(data, null, 2);
        const pv = data.pipeline_verification;
        banner.innerHTML = "<strong>Scenario " + id + " Completed:</strong> Matched Rule: <span style='color:var(--accent)'>" + pv.matched_rule_id + "</span> | Simulated Order: <span style='color:var(--warning)'>" + (pv.simulated_order_cosign_enforced ? "Pending Cosign (Mandatory)" : "None") + "</span> | Audit Chain: <span style='color:var(--success)'>" + (pv.audit_hash_chain_valid ? "Cryptographically Valid (" + pv.total_audit_records + " records)" : "Invalid") + "</span>";
      } catch (err) {
        banner.innerText = "Error: " + err;
      }
    }
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)
