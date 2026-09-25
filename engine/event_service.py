from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.knowledge.service import KnowledgeService
from agents.resolution import ResolutionPipelineResult, SafetyResolutionEngine
from agents.risk.detector import RiskDetector
from agents.risk.schemas import Finding as RiskFinding
from models.models import Event, Finding as DbFinding, Lab, Medication, Order, Patient


class MedicationEventService:
    """
    Service connecting patient medication, lab, and order/status events to deterministic Risk Detection.
    Evaluates complete current patient context:
    - Active medications & medication orders/status
    - Relevant labs & chronological lab trends
    - Validated safety rules & provenance
    - Compares new evaluation with previous finding state (no duplicate unchanged findings)
    - Re-evaluates findings when clinical context changes
    - Executes complete end-to-end resolution, simulated order, explicator, and audit trail.
    """

    def __init__(
        self,
        knowledge_service: KnowledgeService,
        resolution_engine: Optional[SafetyResolutionEngine] = None,
        audit_ledger: Optional[Any] = None,
        rule_context: Optional[Any] = None,
        explicator: Optional[Any] = None,
    ):
        self.knowledge_service = knowledge_service
        self.rule_context = rule_context
        self.audit_ledger = audit_ledger
        self.explicator = explicator

        self.detector = RiskDetector(
            knowledge_service=knowledge_service,
            rule_context=rule_context,
        )

        if resolution_engine:
            self.resolution_engine = resolution_engine
            if not self.resolution_engine.audit_ledger and audit_ledger:
                self.resolution_engine.audit_ledger = audit_ledger
            if not self.resolution_engine.explicator and explicator:
                self.resolution_engine.explicator = explicator
        else:
            self.resolution_engine = SafetyResolutionEngine(
                rule_context=rule_context,
                explicator=explicator,
                audit_ledger=audit_ledger,
            )

    def process_event(
        self,
        db: Session,
        patient_id: int,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        new_medication_name: Optional[str] = None,
    ) -> List[DbFinding]:
        """
        Process a medication, lab, or order/status event against complete patient context:
        1. Ingest event into SQLite `events` table (and log to AuditLedger).
        2. Handle incoming lab data or order status changes from event payload.
        3. Retrieve complete current patient context: active medications, orders, chronological labs, previous findings.
        4. Execute RiskDetector against the complete patient context.
        5. Compare new evaluation with previous finding state:
           - Filter out duplicate unchanged findings.
           - Re-evaluate/update findings when relevant clinical context has changed.
           - Persist new findings.
        6. Return list of affected (new or re-evaluated) DbFinding records.
        """
        payload_data = payload or {}

        try:
            # 1. Record event in SQLite `events` table
            event_record = Event(
                patient_id=patient_id,
                event_type=event_type,
                payload=payload_data,
                created_at=datetime.utcnow(),
            )
            db.add(event_record)

            # 2. Ingest Lab from event if present
            if "test_name" in payload_data and "value" in payload_data:
                lab_record = Lab(
                    patient_id=patient_id,
                    test_name=str(payload_data["test_name"]),
                    value=str(payload_data["value"]),
                    unit=payload_data.get("unit"),
                    reference_range=payload_data.get("reference_range"),
                    measured_at=payload_data.get("measured_at") or datetime.utcnow(),
                    created_at=datetime.utcnow(),
                )
                db.add(lab_record)
                db.flush()

            # Ingest Order status modification if present
            if "status" in payload_data:
                target_status = str(payload_data["status"]).lower().strip()
                if "medication_id" in payload_data:
                    med_to_update = db.get(Medication, payload_data["medication_id"])
                    if med_to_update:
                        med_to_update.status = target_status
                if "order_id" in payload_data:
                    ord_to_update = db.get(Order, payload_data["order_id"])
                    if ord_to_update:
                        ord_to_update.status = target_status
                if "drug_name" in payload_data and target_status in {"discontinued", "cancelled", "inactive"}:
                    meds_by_name = db.execute(
                        select(Medication).where(
                            Medication.patient_id == patient_id,
                            Medication.drug_name == payload_data["drug_name"],
                        )
                    ).scalars().all()
                    for m in meds_by_name:
                        m.status = target_status

            # 3. Retrieve Complete Current Patient Context
            patient = db.execute(
                select(Patient).where(Patient.id == patient_id)
            ).scalar_one_or_none()

            med_stmt = select(Medication).where(
                Medication.patient_id == patient_id,
                Medication.status.in_(["active", "pending", "ordered", None]),
            )
            db_meds = list(db.execute(med_stmt).scalars().all())

            # Include new medication if passed via argument or payload
            med_to_add = new_medication_name or (
                payload_data.get("drug_name")
                if event_type in {"MEDICATION_ORDERED", "ORDER_SUBMITTED", "MEDICATION_ADDED"}
                and payload_data.get("status", "active") not in {"discontinued", "cancelled"}
                else None
            )
            if med_to_add:
                existing_names = [
                    m.drug_name.lower().strip()
                    for m in db_meds
                    if getattr(m, "drug_name", None)
                ]
                if med_to_add.lower().strip() not in existing_names:
                    db_meds.append({"drug_name": med_to_add, "status": "active"})

            # Retrieve Orders and Chronological Labs
            orders_stmt = select(Order).where(Order.patient_id == patient_id)
            orders = list(db.execute(orders_stmt).scalars().all())

            labs_stmt = select(Lab).where(Lab.patient_id == patient_id).order_by(Lab.measured_at.asc(), Lab.id.asc())
            labs = list(db.execute(labs_stmt).scalars().all())

            existing_findings_stmt = select(DbFinding).where(DbFinding.patient_id == patient_id)
            existing_findings = list(db.execute(existing_findings_stmt).scalars().all())

            # 4. Run RiskDetector with Complete Current Context
            detected_findings: List[RiskFinding] = self.detector.detect(
                patient_context=patient,
                medications=db_meds,
                labs=labs,
                orders=orders,
                previous_findings=existing_findings,
                rule_context=self.rule_context,
            )

            if not detected_findings:
                db.commit()
                db.refresh(event_record)
                return []

            # 5. Compare with Previous Finding State: Deduplicate Unchanged, Re-evaluate Changed
            existing_by_sig: Dict[Tuple[str, Tuple[str, ...]], DbFinding] = {}
            for ef in existing_findings:
                rule_id = str(ef.rule_id)
                trace_dict = ef.trace if isinstance(ef.trace, dict) else {}
                matched_pair = tuple(sorted(str(d).lower().strip() for d in trace_dict.get("matched_pair", [])))
                existing_by_sig[(rule_id, matched_pair)] = ef

            new_db_findings: List[DbFinding] = []
            reevaluated_db_findings: List[DbFinding] = []

            for df in detected_findings:
                matched_pair = tuple(sorted(str(d).lower().strip() for d in df.trace.get("matched_pair", [])))
                sig = (str(df.rule_id), matched_pair)

                if sig not in existing_by_sig:
                    # Brand new finding
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
                    existing_by_sig[sig] = db_finding
                else:
                    # Existing finding - check if clinical context has changed
                    ef = existing_by_sig[sig]
                    ef_inputs = ef.inputs if isinstance(ef.inputs, dict) else {}
                    df_inputs = df.inputs if isinstance(df.inputs, dict) else {}

                    context_changed = (
                        ef_inputs.get("inr_context") != df_inputs.get("inr_context")
                        or ef_inputs.get("inr_trend") != df_inputs.get("inr_trend")
                        or ef_inputs.get("inr_value") != df_inputs.get("inr_value")
                        or ef_inputs.get("renal_context") != df_inputs.get("renal_context")
                        or ef_inputs.get("latest_renal_lab") != df_inputs.get("latest_renal_lab")
                        or ef_inputs.get("data_needed") != df_inputs.get("data_needed")
                        or ef.severity != df.severity
                        or ef.description != df.description
                        or ef.action != df.action
                    )

                    if context_changed:
                        # Update finding in-place to reflect updated clinical context
                        ef.severity = df.severity
                        ef.title = df.title
                        ef.description = df.description
                        ef.action = df.action
                        ef.inputs = df.inputs
                        ef.trace = df.trace
                        reevaluated_db_findings.append(ef)
                    else:
                        # Unchanged finding: do NOT create duplicate row
                        pass

            db.commit()
            db.refresh(event_record)
            for f in new_db_findings:
                db.refresh(f)
            for f in reevaluated_db_findings:
                db.refresh(f)

            # 6. Audit Logging in Hash-Chained Ledger
            if self.audit_ledger:
                for f in new_db_findings:
                    trace_dict = f.trace if isinstance(f.trace, dict) else {}
                    self.audit_ledger.append_event(
                        actor="risk_detector",
                        event_type="RISK_FINDING_DETECTED",
                        payload={
                            "finding_id": f.id,
                            "patient_id": f.patient_id,
                            "rule_id": f.rule_id,
                            "severity": f.severity,
                            "title": f.title,
                            "source": trace_dict.get("source", "Unknown"),
                            "evidence_id": trace_dict.get("evidence_id"),
                        },
                    )
                for f in reevaluated_db_findings:
                    trace_dict = f.trace if isinstance(f.trace, dict) else {}
                    self.audit_ledger.append_event(
                        actor="risk_detector",
                        event_type="RISK_FINDING_REEVALUATED",
                        payload={
                            "finding_id": f.id,
                            "patient_id": f.patient_id,
                            "rule_id": f.rule_id,
                            "severity": f.severity,
                            "title": f.title,
                            "source": trace_dict.get("source", "Unknown"),
                            "evidence_id": trace_dict.get("evidence_id"),
                        },
                    )

            return new_db_findings + reevaluated_db_findings
        except Exception:
            db.rollback()
            raise

    def process_event_with_resolutions(
        self,
        db: Session,
        patient_id: int,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        new_medication_name: Optional[str] = None,
        rule_context: Optional[Any] = None,
    ) -> Tuple[List[DbFinding], List[ResolutionPipelineResult]]:
        """
        Process event against complete patient context and execute the complete
        deterministic resolution pipeline:
        Event -> MedicationEventService / Ingest -> RiskDetector -> Finding
        -> SafetyResolutionEngine -> candidate filtering + ranking
        -> SimulatedOrder -> Explicator -> AuditLedger.
        """
        try:
            affected_findings = self.process_event(
                db=db,
                patient_id=patient_id,
                event_type=event_type,
                payload=payload,
                new_medication_name=new_medication_name,
            )
        except Exception:
            db.rollback()
            raise

        ctx_to_use = rule_context or self.rule_context
        engine = self.resolution_engine or SafetyResolutionEngine(
            rule_context=ctx_to_use,
            explicator=self.explicator,
            audit_ledger=self.audit_ledger,
        )

        if not affected_findings:
            return affected_findings, []

        med_stmt = select(Medication).where(
            Medication.patient_id == patient_id,
            Medication.status.in_(["active", "pending", "ordered", None]),
        )
        active_meds = db.execute(med_stmt).scalars().all()

        pipeline_results = engine.resolve_findings_pipeline(
            findings=affected_findings,
            rule_context=ctx_to_use,
            existing_medications=list(active_meds),
        )
        return affected_findings, pipeline_results

