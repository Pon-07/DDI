"""ML severity advisories (non-clinical, advisory only).

These endpoints are deliberately separate from /findings and /patients: nothing here creates, edits or
re-grades a rule-based finding. They return an estimate with its reliability, explanation and limitations.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.audit.ledger import AuditLedger
from app.dependencies import get_audit_ledger
from database.database import get_db
from ml.advisory import DISCLAIMER, LIMITATIONS, MLSeverityAdvisory, SeverityAdvisor, get_advisor
from models import Finding, Patient

router = APIRouter(prefix="/ml", tags=["ML severity advisory (advisory only, not clinical)"])


class PairAdvisoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drug_a: str = Field(..., min_length=1, max_length=255)
    drug_b: str = Field(..., min_length=1, max_length=255)
    interaction_documented: bool = Field(
        False, description="Set true only if a validated source already documents an interaction for this pair")


class PairAdvisoryResponse(BaseModel):
    advisory: MLSeverityAdvisory
    notice: str


class FindingAdvisory(BaseModel):
    finding_id: Optional[int] = None
    rule_id: Optional[str] = None
    rule_severity: Optional[str] = Field(None, description="Severity exactly as the rule engine stored it; unchanged")
    advisory: MLSeverityAdvisory


class PatientAdvisoryResponse(BaseModel):
    patient_id: int
    rule_findings_unchanged: bool = True
    advisories: List[FindingAdvisory]
    disclaimer: str = DISCLAIMER


def _advisor() -> SeverityAdvisor:
    advisor = get_advisor()
    if advisor is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="ML severity advisory is disabled or its model artifact is unavailable. "
                                   "Rule-based safety findings are unaffected.")
    return advisor


@router.get("/status", summary="Model status and honest performance summary (no patient data)")
def ml_status() -> Dict[str, Any]:
    advisor = get_advisor()
    if advisor is None:
        return {"enabled": False, "reason": "disabled by AEGIS_ML_ADVISORY or artifact missing"}
    meta, summ = advisor.model.meta, advisor.model.validation.get("summary", {})
    return {
        "enabled": True,
        "model": meta["model_name"],
        "trained_at_utc": meta["trained_at_utc"],
        "n_training_pairs": meta["n_training_pairs"],
        "class_counts": meta["class_counts"],
        "intended_use": meta["intended_use"],
        "not_for": meta["not_for"],
        "held_out_macro_f1": {r: round(summ[r]["model"]["macro_f1"]["mean"], 3) for r in summ},
        "chance_macro_f1": 0.33,
        "uses_who_atc_data": bool(meta.get("uses_who_atc_data", False)),
        "clinically_validated": False,
        "limitations": LIMITATIONS,
        "disclaimer": DISCLAIMER,
    }


@router.post("/severity-advisory", response_model=PairAdvisoryResponse,
             summary="Advisory severity estimate for a drug pair")
def pair_advisory(payload: PairAdvisoryRequest):
    unknown = _advisor().unrecognised(payload.drug_a, payload.drug_b)
    if unknown:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Drug not recognised by the model (not in DDInter training data or the WHO ATC index): "
                                   + ", ".join(repr(u) for u in unknown) + ". No estimate is given for unknown drugs.")
    adv = _advisor().advise_pair(payload.drug_a, payload.drug_b)
    if adv is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Could not resolve two distinct drug names from the input.")
    notice = ("Estimate of severity for an interaction documented elsewhere." if payload.interaction_documented else
              "No interaction has been established for this pair. This model cannot tell whether the drugs interact; "
              "read the estimate only as 'if they interact, the severity pattern resembles X'.")
    return PairAdvisoryResponse(advisory=adv, notice=notice)


@router.get("/patients/{patient_id}/severity-advisories", response_model=PatientAdvisoryResponse,
            summary="Advisories next to the patient's stored rule findings")
def patient_advisories(
    patient_id: int,
    db: Session = Depends(get_db),
    ledger: AuditLedger = Depends(get_audit_ledger),
):
    advisor = _advisor()
    if db.execute(select(Patient.id).where(Patient.id == patient_id)).scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Patient with ID {patient_id} not found.")
    findings = db.execute(select(Finding).where(Finding.patient_id == patient_id).order_by(Finding.id)).scalars().all()
    items = advisor.advise_findings(findings)
    try:
        ledger.append_event(
            actor="system",
            event_type="ML_SEVERITY_ADVISORY_VIEWED",
            payload={"patient_id": patient_id, "finding_ids": [i["finding_id"] for i in items],
                     "model": advisor.model.meta["model_name"], "advisory_only": True},
        )
    except Exception:  # auditing must never break a read-only advisory
        pass
    return PatientAdvisoryResponse(patient_id=patient_id, advisories=[FindingAdvisory(**i) for i in items])
