from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.knowledge.service import KnowledgeService
from agents.risk.detector import RiskDetector
from agents.risk.schemas import Finding as RiskFinding
from models.models import Event, Finding as DbFinding, Medication, Patient


class MedicationEventService:
    """
    Service connecting patient medication/order events to deterministic Risk Detection.
    Retrieves active medications from SQLite, detects drug interaction findings,
    records the event, and stores non-duplicate findings in SQLite.
    """

    def __init__(self, knowledge_service: KnowledgeService):
        self.knowledge_service = knowledge_service
        self.detector = RiskDetector(knowledge_service=knowledge_service)

    def process_event(
        self,
        db: Session,
        patient_id: int,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        new_medication_name: Optional[str] = None,
    ) -> List[DbFinding]:
        """
        Process a medication/order event:
        1. Record the event in SQLite `events` table.
        2. Retrieve patient and active medications from SQLite.
        3. Combine active medications with incoming event medication (if not already recorded).
        4. Execute RiskDetector.
        5. Filter out duplicate findings already recorded for this patient.
        6. Persist new findings in SQLite `findings` table.
        7. Return list of newly stored DbFinding records.
        """
        # 1. Record event in `events` table
        event_record = Event(
            patient_id=patient_id,
            event_type=event_type,
            payload=payload or {},
            created_at=datetime.utcnow(),
        )
        db.add(event_record)

        # 2. Retrieve patient context
        patient = db.execute(
            select(Patient).where(Patient.id == patient_id)
        ).scalar_one_or_none()

        # 3. Retrieve patient's active medications from SQLite
        med_stmt = (
            select(Medication)
            .where(
                Medication.patient_id == patient_id,
                Medication.status.in_(["active", "pending", "ordered", None]),
            )
        )
        db_meds = db.execute(med_stmt).scalars().all()

        med_list: List[Any] = list(db_meds)

        # If a new medication is part of the event payload and not yet in db_meds, include it
        if new_medication_name:
            existing_names = [
                m.drug_name.lower().strip()
                for m in db_meds
                if getattr(m, "drug_name", None)
            ]
            if new_medication_name.lower().strip() not in existing_names:
                med_list.append({"drug_name": new_medication_name, "status": "active"})

        # 4. Run RiskDetector
        detected_findings: List[RiskFinding] = self.detector.detect(
            patient_context=patient,
            medications=med_list,
        )

        if not detected_findings:
            db.commit()
            db.refresh(event_record)
            return []

        # 5. Check existing findings for this patient to prevent duplicate rows
        existing_findings_stmt = select(DbFinding).where(
            DbFinding.patient_id == patient_id
        )
        existing_findings = db.execute(existing_findings_stmt).scalars().all()

        existing_signatures: Set[Tuple[str, Tuple[str, ...]]] = set()
        for ef in existing_findings:
            rule_id = str(ef.rule_id)
            trace_dict = ef.trace if isinstance(ef.trace, dict) else {}
            matched_pair = tuple(sorted(trace_dict.get("matched_pair", [])))
            existing_signatures.add((rule_id, matched_pair))

        new_db_findings: List[DbFinding] = []

        for df in detected_findings:
            matched_pair = tuple(sorted(df.trace.get("matched_pair", [])))
            sig = (str(df.rule_id), matched_pair)

            if sig in existing_signatures:
                continue

            existing_signatures.add(sig)

            db_finding = DbFinding(
                patient_id=patient_id,
                rule_id=df.rule_id,
                severity=df.severity,
                title=df.title,
                description=df.description,
                action=df.action,
                inputs=df.inputs,
                trace=df.trace,
                created_at=datetime.utcnow(),
            )
            db.add(db_finding)
            new_db_findings.append(db_finding)

        db.commit()
        db.refresh(event_record)
        for f in new_db_findings:
            db.refresh(f)

        return new_db_findings
