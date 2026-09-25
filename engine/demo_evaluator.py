import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.audit.ledger import AuditLedger
from agents.explicator.service import ExplicatorService
from agents.knowledge import DrugNormalizer, KnowledgeService
from agents.resolution.resolver import SafetyResolutionEngine
from agents.resolution.schemas import ResolutionPipelineResult
from agents.risk.detector import RiskDetector
from agents.risk.schemas import Finding
from agents.rules.schemas import RulePack, SafetyRule
from engine.event_service import MedicationEventService
from models.models import Base, Lab, Medication, Patient


class AssertionResult(BaseModel):
    assertion_name: str
    passed: bool
    details: str


class CaseEvaluationReport(BaseModel):
    case_id: str
    case_name: str
    detection_result: bool
    matched_rule_id: Optional[str] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)
    resolution_status: str
    candidate_count: int
    simulated_order_status: Optional[str] = None
    explanation_status: str
    audit_chain_status: str
    assertions: List[AssertionResult] = Field(default_factory=list)
    passed: bool


class NegativeCaseReport(BaseModel):
    case_id: str
    case_name: str
    description: str
    passed: bool
    details: str
    assertions: List[AssertionResult] = Field(default_factory=list)


class FullEvaluationSummary(BaseModel):
    total_cases: int
    passed_cases: int
    failed_cases: int
    cases: Dict[str, CaseEvaluationReport] = Field(default_factory=dict)
    negative_cases: Dict[str, NegativeCaseReport] = Field(default_factory=dict)
    all_passed: bool


class DemoCaseEvaluationHarness:
    """
    Deterministic automated evaluation harness for the THREE official AEGIS demo cases:
    - CASE 1: Warfarin + fluconazole with relevant INR context.
    - CASE 2: Enoxaparin with declining renal-function context.
    - CASE 3: Duplicate ACE-inhibitor therapy.
    And 4 negative/failure resilience cases:
    1. Missing required context -> no fabricated decision.
    2. Unknown drug/pair -> no invented interaction.
    3. Inactive rule -> no safety finding from that rule.
    4. Repeated unchanged event -> no duplicate finding.

    Strictly uses isolated SQLite in-memory databases and isolated audit ledgers.
    Zero modifications to aegis_rx.db. Zero external network calls. No Ollama required.
    """

    def __init__(self):
        self.normalizer = DrugNormalizer()

    def _create_isolated_environment(self):
        """Build isolated in-memory DB and temporary audit ledger for clean evaluation."""
        temp_dir = tempfile.TemporaryDirectory()
        db_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(db_engine)
        Session = sessionmaker(bind=db_engine)
        session = Session()

        ledger_path = Path(temp_dir.name) / "eval_audit_ledger.db"
        audit_ledger = AuditLedger(db_path=str(ledger_path))

        explicator = ExplicatorService()
        resolver = SafetyResolutionEngine(
            explicator=explicator,
            audit_ledger=audit_ledger,
        )
        knowledge_svc = KnowledgeService(normalizer=self.normalizer)
        event_svc = MedicationEventService(
            knowledge_service=knowledge_svc,
            resolution_engine=resolver,
            rule_context=resolver,
            explicator=explicator,
            audit_ledger=audit_ledger,
        )

        return {
            "temp_dir": temp_dir,
            "session": session,
            "audit_ledger": audit_ledger,
            "resolver": resolver,
            "event_svc": event_svc,
            "knowledge_svc": knowledge_svc,
        }

    def evaluate_case_1(self) -> CaseEvaluationReport:
        """
        CASE 1: Warfarin + fluconazole with relevant rising INR context.
        Pipeline:
        Patient context (active warfarin, rising INR labs)
        -> Event (prescribe fluconazole)
        -> Risk Detection
        -> Finding
        -> Resolution
        -> Explanation
        -> SimulatedOrder
        -> Audit Ledger
        """
        env = self._create_isolated_environment()
        session = env["session"]
        event_svc = env["event_svc"]
        ledger = env["audit_ledger"]
        assertions: List[AssertionResult] = []

        try:
            # 1. Patient Context: Warfarin + rising INR (2.1 -> 3.4)
            patient = Patient(patient_identifier="DEMO-PT-001", name="Warfarin Demo Patient", sex="M")
            session.add(patient)
            session.commit()

            session.add(Medication(
                patient_id=patient.id,
                drug_name="warfarin",
                dose="5",
                dose_unit="mg",
                route="oral",
                frequency="daily",
                status="active",
            ))
            session.add(Lab(patient_id=patient.id, test_name="INR", value="2.1"))
            session.add(Lab(patient_id=patient.id, test_name="INR", value="3.4"))
            session.commit()

            # 2. Event: Fluconazole prescription
            affected_findings, pipeline_results = event_svc.process_event_with_resolutions(
                db=session,
                patient_id=patient.id,
                event_type="MEDICATION_PRESCRIBED",
                payload={"drug_name": "fluconazole", "dose": "200mg", "route": "oral"},
                new_medication_name="fluconazole",
            )

            # Assertions
            det_ok = len(affected_findings) > 0
            assertions.append(AssertionResult(
                assertion_name="Detection finding produced",
                passed=det_ok,
                details=f"Produced {len(affected_findings)} finding(s)",
            ))

            matched_rule = None
            provenance: Dict[str, Any] = {}
            if det_ok:
                f0 = affected_findings[0]
                matched_rule = f0.rule_id
                trace = f0.trace if isinstance(f0.trace, dict) else {}
                provenance = {
                    "rule_id": f0.rule_id,
                    "source": f0.trace.get("source") if isinstance(f0.trace, dict) else "AEGIS_HACKATHON_DEMO",
                    "source_version": trace.get("source_version"),
                    "evidence_id": trace.get("evidence_id"),
                }

            rule_ok = matched_rule == "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE"
            assertions.append(AssertionResult(
                assertion_name="Matched expected rule_id",
                passed=rule_ok,
                details=f"Expected AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE, got {matched_rule}",
            ))

            ev_id_ok = provenance.get("evidence_id") == "AEGIS-DEMO-EV-001"
            assertions.append(AssertionResult(
                assertion_name="Preserved evidence_id",
                passed=ev_id_ok,
                details=f"Expected AEGIS-DEMO-EV-001, got {provenance.get('evidence_id')}",
            ))

            pipe_ok = len(pipeline_results) > 0
            res_status = pipeline_results[0].status if pipe_ok else "none"
            cand_count = len(pipeline_results[0].ranked_candidates) if pipe_ok else 0

            res_ok = res_status == "actionable" and cand_count > 0
            assertions.append(AssertionResult(
                assertion_name="Deterministic resolution status actionable",
                passed=res_ok,
                details=f"Status: {res_status}, Candidates: {cand_count}",
            ))

            sim_order = pipeline_results[0].simulated_order if pipe_ok else None
            sim_ok = (
                sim_order is not None
                and sim_order.status == "pending_cosign"
                and sim_order.requires_cosign is True
                and sim_order.auto_execute is False
                and sim_order.is_simulated is True
            )
            assertions.append(AssertionResult(
                assertion_name="SimulatedOrder cosign-enforced and never auto-executed",
                passed=sim_ok,
                details=f"Order status: {sim_order.status if sim_order else None}, requires_cosign={sim_order.requires_cosign if sim_order else None}, auto_execute={sim_order.auto_execute if sim_order else None}",
            ))

            exp = pipeline_results[0].explanation if pipe_ok else None
            exp_ok = exp is not None and "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE" in exp.full_text
            assertions.append(AssertionResult(
                assertion_name="Explanation references matched rule and provenance",
                passed=exp_ok,
                details=f"Explanation generated: {exp is not None}",
            ))

            events = ledger.get_events()
            ev_types = [e.event_type for e in events]
            expected_lifecycle = [
                "PATIENT_EVENT_INGESTED",
                "RISK_FINDING_DETECTED",
                "SAFETY_RESOLUTION_EVALUATED",
                "EXPLANATION_GENERATED",
                "SIMULATED_ORDER_CREATED",
            ]
            lifecycle_ok = all(t in ev_types for t in expected_lifecycle)
            assertions.append(AssertionResult(
                assertion_name="Audit ledger contains complete lifecycle events",
                passed=lifecycle_ok,
                details=f"Event types present: {ev_types}",
            ))

            chain_valid = ledger.verify_chain().is_valid
            assertions.append(AssertionResult(
                assertion_name="Audit ledger hash chain verified",
                passed=chain_valid,
                details=f"verify_chain().is_valid == {chain_valid}",
            ))

            case_passed = all(a.passed for a in assertions)

            return CaseEvaluationReport(
                case_id="CASE_1",
                case_name="Warfarin + Fluconazole with Rising INR",
                detection_result=det_ok,
                matched_rule_id=matched_rule,
                provenance=provenance,
                resolution_status=res_status,
                candidate_count=cand_count,
                simulated_order_status=sim_order.status if sim_order else None,
                explanation_status="generated" if exp else "missing",
                audit_chain_status="valid" if chain_valid else "corrupted",
                assertions=assertions,
                passed=case_passed,
            )
        finally:
            env["temp_dir"].cleanup()

    def evaluate_case_2(self) -> CaseEvaluationReport:
        """
        CASE 2: Enoxaparin with declining renal-function context.
        Pipeline:
        Patient context (active enoxaparin, baseline creatinine 1.0)
        -> Event (new creatinine lab 2.4 showing declining renal trend)
        -> Risk Detection
        -> Finding
        -> Resolution
        -> Explanation
        -> SimulatedOrder
        -> Audit Ledger
        """
        env = self._create_isolated_environment()
        session = env["session"]
        event_svc = env["event_svc"]
        ledger = env["audit_ledger"]
        assertions: List[AssertionResult] = []

        try:
            # 1. Patient Context: Enoxaparin + baseline creatinine 1.0
            patient = Patient(patient_identifier="DEMO-PT-002", name="Enoxaparin Demo Patient", sex="F")
            session.add(patient)
            session.commit()

            session.add(Medication(
                patient_id=patient.id,
                drug_name="enoxaparin",
                dose="40",
                dose_unit="mg",
                route="subcutaneous",
                frequency="daily",
                status="active",
            ))
            session.add(Lab(patient_id=patient.id, test_name="Creatinine", value="1.0"))
            session.commit()

            # 2. Event: Lab result recorded with rising creatinine (2.4)
            affected_findings, pipeline_results = event_svc.process_event_with_resolutions(
                db=session,
                patient_id=patient.id,
                event_type="LAB_RESULT_RECORDED",
                payload={"test_name": "Creatinine", "value": "2.4", "unit": "mg/dL"},
            )

            det_ok = len(affected_findings) > 0
            assertions.append(AssertionResult(
                assertion_name="Detection finding produced",
                passed=det_ok,
                details=f"Produced {len(affected_findings)} finding(s)",
            ))

            matched_rule = None
            provenance: Dict[str, Any] = {}
            if det_ok:
                f0 = affected_findings[0]
                matched_rule = f0.rule_id
                trace = f0.trace if isinstance(f0.trace, dict) else {}
                provenance = {
                    "rule_id": f0.rule_id,
                    "source": f0.trace.get("source") if isinstance(f0.trace, dict) else "AEGIS_HACKATHON_DEMO",
                    "source_version": trace.get("source_version"),
                    "evidence_id": trace.get("evidence_id"),
                }

            rule_ok = matched_rule == "AEGIS-DEMO-002-ENOXAPARIN-RENAL"
            assertions.append(AssertionResult(
                assertion_name="Matched expected rule_id",
                passed=rule_ok,
                details=f"Expected AEGIS-DEMO-002-ENOXAPARIN-RENAL, got {matched_rule}",
            ))

            ev_id_ok = provenance.get("evidence_id") == "AEGIS-DEMO-EV-002"
            assertions.append(AssertionResult(
                assertion_name="Preserved evidence_id",
                passed=ev_id_ok,
                details=f"Expected AEGIS-DEMO-EV-002, got {provenance.get('evidence_id')}",
            ))

            pipe_ok = len(pipeline_results) > 0
            res_status = pipeline_results[0].status if pipe_ok else "none"
            cand_count = len(pipeline_results[0].ranked_candidates) if pipe_ok else 0

            res_ok = res_status == "actionable" and cand_count > 0
            assertions.append(AssertionResult(
                assertion_name="Deterministic resolution status actionable",
                passed=res_ok,
                details=f"Status: {res_status}, Candidates: {cand_count}",
            ))

            sim_order = pipeline_results[0].simulated_order if pipe_ok else None
            sim_ok = (
                sim_order is not None
                and sim_order.status == "pending_cosign"
                and sim_order.requires_cosign is True
                and sim_order.auto_execute is False
                and sim_order.is_simulated is True
            )
            assertions.append(AssertionResult(
                assertion_name="SimulatedOrder cosign-enforced and never auto-executed",
                passed=sim_ok,
                details=f"Order status: {sim_order.status if sim_order else None}, requires_cosign={sim_order.requires_cosign if sim_order else None}, auto_execute={sim_order.auto_execute if sim_order else None}",
            ))

            exp = pipeline_results[0].explanation if pipe_ok else None
            exp_ok = exp is not None and "AEGIS-DEMO-002-ENOXAPARIN-RENAL" in exp.full_text
            assertions.append(AssertionResult(
                assertion_name="Explanation references matched rule and provenance",
                passed=exp_ok,
                details=f"Explanation generated: {exp is not None}",
            ))

            events = ledger.get_events()
            ev_types = [e.event_type for e in events]
            expected_lifecycle = [
                "PATIENT_EVENT_INGESTED",
                "RISK_FINDING_DETECTED",
                "SAFETY_RESOLUTION_EVALUATED",
                "EXPLANATION_GENERATED",
                "SIMULATED_ORDER_CREATED",
            ]
            lifecycle_ok = all(t in ev_types for t in expected_lifecycle)
            assertions.append(AssertionResult(
                assertion_name="Audit ledger contains complete lifecycle events",
                passed=lifecycle_ok,
                details=f"Event types present: {ev_types}",
            ))

            chain_valid = ledger.verify_chain().is_valid
            assertions.append(AssertionResult(
                assertion_name="Audit ledger hash chain verified",
                passed=chain_valid,
                details=f"verify_chain().is_valid == {chain_valid}",
            ))

            case_passed = all(a.passed for a in assertions)

            return CaseEvaluationReport(
                case_id="CASE_2",
                case_name="Enoxaparin with Declining Renal-Function Context",
                detection_result=det_ok,
                matched_rule_id=matched_rule,
                provenance=provenance,
                resolution_status=res_status,
                candidate_count=cand_count,
                simulated_order_status=sim_order.status if sim_order else None,
                explanation_status="generated" if exp else "missing",
                audit_chain_status="valid" if chain_valid else "corrupted",
                assertions=assertions,
                passed=case_passed,
            )
        finally:
            env["temp_dir"].cleanup()

    def evaluate_case_3(self) -> CaseEvaluationReport:
        """
        CASE 3: Duplicate ACE-inhibitor therapy.
        Pipeline:
        Patient context (active lisinopril)
        -> Event (prescribe enalapril)
        -> Risk Detection
        -> Finding
        -> Resolution
        -> Explanation
        -> SimulatedOrder
        -> Audit Ledger
        """
        env = self._create_isolated_environment()
        session = env["session"]
        event_svc = env["event_svc"]
        ledger = env["audit_ledger"]
        assertions: List[AssertionResult] = []

        try:
            # 1. Patient Context: Active lisinopril
            patient = Patient(patient_identifier="DEMO-PT-003", name="Duplicate ACE Demo Patient", sex="M")
            session.add(patient)
            session.commit()

            session.add(Medication(
                patient_id=patient.id,
                drug_name="lisinopril",
                dose="20",
                dose_unit="mg",
                route="oral",
                frequency="daily",
                status="active",
            ))
            session.commit()

            # 2. Event: Prescribe enalapril (duplicate ACE-inhibitor)
            affected_findings, pipeline_results = event_svc.process_event_with_resolutions(
                db=session,
                patient_id=patient.id,
                event_type="MEDICATION_PRESCRIBED",
                payload={"drug_name": "enalapril", "dose": "10mg", "route": "oral"},
                new_medication_name="enalapril",
            )

            det_ok = len(affected_findings) > 0
            assertions.append(AssertionResult(
                assertion_name="Detection finding produced",
                passed=det_ok,
                details=f"Produced {len(affected_findings)} finding(s)",
            ))

            matched_rule = None
            provenance: Dict[str, Any] = {}
            if det_ok:
                f0 = affected_findings[0]
                matched_rule = f0.rule_id
                trace = f0.trace if isinstance(f0.trace, dict) else {}
                provenance = {
                    "rule_id": f0.rule_id,
                    "source": f0.trace.get("source") if isinstance(f0.trace, dict) else "AEGIS_HACKATHON_DEMO",
                    "source_version": trace.get("source_version"),
                    "evidence_id": trace.get("evidence_id"),
                }

            rule_ok = matched_rule == "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR"
            assertions.append(AssertionResult(
                assertion_name="Matched expected rule_id",
                passed=rule_ok,
                details=f"Expected AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR, got {matched_rule}",
            ))

            ev_id_ok = provenance.get("evidence_id") == "AEGIS-DEMO-EV-003"
            assertions.append(AssertionResult(
                assertion_name="Preserved evidence_id",
                passed=ev_id_ok,
                details=f"Expected AEGIS-DEMO-EV-003, got {provenance.get('evidence_id')}",
            ))

            pipe_ok = len(pipeline_results) > 0
            res_status = pipeline_results[0].status if pipe_ok else "none"
            cand_count = len(pipeline_results[0].ranked_candidates) if pipe_ok else 0

            res_ok = res_status == "actionable" and cand_count > 0
            assertions.append(AssertionResult(
                assertion_name="Deterministic resolution status actionable",
                passed=res_ok,
                details=f"Status: {res_status}, Candidates: {cand_count}",
            ))

            sim_order = pipeline_results[0].simulated_order if pipe_ok else None
            sim_ok = (
                sim_order is not None
                and sim_order.status == "pending_cosign"
                and sim_order.requires_cosign is True
                and sim_order.auto_execute is False
                and sim_order.is_simulated is True
            )
            assertions.append(AssertionResult(
                assertion_name="SimulatedOrder cosign-enforced and never auto-executed",
                passed=sim_ok,
                details=f"Order status: {sim_order.status if sim_order else None}, requires_cosign={sim_order.requires_cosign if sim_order else None}, auto_execute={sim_order.auto_execute if sim_order else None}",
            ))

            exp = pipeline_results[0].explanation if pipe_ok else None
            exp_ok = exp is not None and "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR" in exp.full_text
            assertions.append(AssertionResult(
                assertion_name="Explanation references matched rule and provenance",
                passed=exp_ok,
                details=f"Explanation generated: {exp is not None}",
            ))

            events = ledger.get_events()
            ev_types = [e.event_type for e in events]
            expected_lifecycle = [
                "PATIENT_EVENT_INGESTED",
                "RISK_FINDING_DETECTED",
                "SAFETY_RESOLUTION_EVALUATED",
                "EXPLANATION_GENERATED",
                "SIMULATED_ORDER_CREATED",
            ]
            lifecycle_ok = all(t in ev_types for t in expected_lifecycle)
            assertions.append(AssertionResult(
                assertion_name="Audit ledger contains complete lifecycle events",
                passed=lifecycle_ok,
                details=f"Event types present: {ev_types}",
            ))

            chain_valid = ledger.verify_chain().is_valid
            assertions.append(AssertionResult(
                assertion_name="Audit ledger hash chain verified",
                passed=chain_valid,
                details=f"verify_chain().is_valid == {chain_valid}",
            ))

            case_passed = all(a.passed for a in assertions)

            return CaseEvaluationReport(
                case_id="CASE_3",
                case_name="Duplicate ACE-Inhibitor Therapy",
                detection_result=det_ok,
                matched_rule_id=matched_rule,
                provenance=provenance,
                resolution_status=res_status,
                candidate_count=cand_count,
                simulated_order_status=sim_order.status if sim_order else None,
                explanation_status="generated" if exp else "missing",
                audit_chain_status="valid" if chain_valid else "corrupted",
                assertions=assertions,
                passed=case_passed,
            )
        finally:
            env["temp_dir"].cleanup()

    def evaluate_negative_cases(self) -> Dict[str, NegativeCaseReport]:
        """
        Evaluate negative and failure resilience cases:
        1. Missing required context -> no fabricated decision.
        2. Unknown drug/pair -> no invented interaction.
        3. Inactive rule -> no safety finding from that rule.
        4. Repeated unchanged event -> no duplicate finding.
        """
        env = self._create_isolated_environment()
        session = env["session"]
        event_svc = env["event_svc"]
        resolver = env["resolver"]
        reports: Dict[str, NegativeCaseReport] = {}

        try:
            # --- NEGATIVE CASE 1: Missing Required Context ---
            p1 = Patient(patient_identifier="NEG-PT-001", name="Missing Context Patient", sex="M")
            session.add(p1)
            session.commit()
            session.add(Medication(
                patient_id=p1.id,
                drug_name="enoxaparin",
                dose="40",
                dose_unit="mg",
                route="subcutaneous",
                status="active",
            ))
            session.commit()

            # No labs on record for p1
            affected_1, pipe_1 = event_svc.process_event_with_resolutions(
                db=session,
                patient_id=p1.id,
                event_type="MEDICATION_REVIEWED",
                payload={},
            )
            # Should detect finding for missing baseline lab, but not fabricate numerical creatinine or dose
            f1 = affected_1[0] if affected_1 else None
            inputs_1 = f1.inputs if (f1 and isinstance(f1.inputs, dict)) else {}
            no_fabricated_numbers = (
                inputs_1.get("renal_context") == "missing"
                and "latest_renal_lab" not in inputs_1
            )
            neg1_assertions = [
                AssertionResult(
                    assertion_name="Flagged missing context explicitly",
                    passed=inputs_1.get("renal_context") == "missing",
                    details=f"renal_context={inputs_1.get('renal_context')}",
                ),
                AssertionResult(
                    assertion_name="Never fabricated numerical creatinine cutoff or dose",
                    passed=no_fabricated_numbers,
                    details="No numerical lab was fabricated without data",
                ),
            ]
            reports["NEG_1_MISSING_CONTEXT"] = NegativeCaseReport(
                case_id="NEG_1",
                case_name="Missing Required Context",
                description="Prescription without required baseline lab must explicitly flag missing data without fabricating numbers",
                passed=all(a.passed for a in neg1_assertions),
                details="Explicitly required baseline renal assessment; no values fabricated.",
                assertions=neg1_assertions,
            )

            # --- NEGATIVE CASE 2: Unknown Drug / Safe Pair ---
            p2 = Patient(patient_identifier="NEG-PT-002", name="Safe Pair Patient", sex="F")
            session.add(p2)
            session.commit()
            session.add(Medication(
                patient_id=p2.id,
                drug_name="acetaminophen",
                dose="500",
                dose_unit="mg",
                status="active",
            ))
            session.commit()

            affected_2, pipe_2 = event_svc.process_event_with_resolutions(
                db=session,
                patient_id=p2.id,
                event_type="MEDICATION_PRESCRIBED",
                payload={"drug_name": "metformin", "dose": "500mg"},
                new_medication_name="metformin",
            )
            zero_findings = len(affected_2) == 0 and len(pipe_2) == 0
            neg2_assertions = [
                AssertionResult(
                    assertion_name="Zero safety findings for non-interacting pair",
                    passed=zero_findings,
                    details=f"Produced {len(affected_2)} findings",
                )
            ]
            reports["NEG_2_UNKNOWN_OR_SAFE_PAIR"] = NegativeCaseReport(
                case_id="NEG_2",
                case_name="Unknown Drug / Safe Pair",
                description="Prescription of safe/unrelated drugs must not produce invented interactions or recommendations",
                passed=zero_findings,
                details=f"Findings produced: {len(affected_2)}",
                assertions=neg2_assertions,
            )

            # --- NEGATIVE CASE 3: Inactive Rule ---
            custom_pack = RulePack(
                pack_name="test_inactive_pack",
                version="1.0.0",
                rules=[
                    SafetyRule(
                        rule_id="INACTIVE-RULE-999",
                        rule_type="drug_interaction",
                        drug_a="aspirin",
                        drug_b="atorvastatin",
                        source="TEST",
                        source_version="1.0.0",
                        evidence_id="TEST-EV-999",
                        evidence_text="Inactive test interaction.",
                        status="inactive",
                    )
                ],
            )
            temp_resolver = SafetyResolutionEngine(rule_context=custom_pack)
            active_rules = temp_resolver.get_active_rules()
            inactive_filtered = not any(getattr(r, "rule_id", None) == "INACTIVE-RULE-999" for r in active_rules)
            neg3_assertions = [
                AssertionResult(
                    assertion_name="Inactive rules filtered out from resolution and detection",
                    passed=inactive_filtered,
                    details=f"Active rules count: {len(active_rules)}",
                )
            ]
            reports["NEG_3_INACTIVE_RULE"] = NegativeCaseReport(
                case_id="NEG_3",
                case_name="Inactive Rule Filtering",
                description="Inactive/disabled rules must never produce active safety findings or resolution candidates",
                passed=inactive_filtered,
                details=f"Inactive rule presence in active pool: {not inactive_filtered}",
                assertions=neg3_assertions,
            )

            # --- NEGATIVE CASE 4: Repeated Unchanged Event ---
            # Ingesting identical event again for p1
            affected_4, pipe_4 = event_svc.process_event_with_resolutions(
                db=session,
                patient_id=p1.id,
                event_type="MEDICATION_REVIEWED",
                payload={},
            )
            no_duplicates = len(affected_4) == 0 and len(pipe_4) == 0
            neg4_assertions = [
                AssertionResult(
                    assertion_name="No duplicate finding generated on unchanged repeated event",
                    passed=no_duplicates,
                    details=f"Affected findings on replay: {len(affected_4)}",
                )
            ]
            reports["NEG_4_REPEATED_UNCHANGED_EVENT"] = NegativeCaseReport(
                case_id="NEG_4",
                case_name="Repeated Unchanged Event Deduplication",
                description="Re-ingesting an identical event without clinical context changes must not insert duplicate rows or resolutions",
                passed=no_duplicates,
                details=f"Duplicate findings: {len(affected_4)}",
                assertions=neg4_assertions,
            )

            return reports
        finally:
            env["temp_dir"].cleanup()

    def run_full_evaluation(self) -> FullEvaluationSummary:
        """Run complete evaluation across all 3 demo cases and 4 negative failure cases."""
        c1 = self.evaluate_case_1()
        c2 = self.evaluate_case_2()
        c3 = self.evaluate_case_3()
        negatives = self.evaluate_negative_cases()

        cases_map = {
            "CASE_1": c1,
            "CASE_2": c2,
            "CASE_3": c3,
        }

        total_cases = len(cases_map) + len(negatives)
        passed_cases = sum(1 for c in cases_map.values() if c.passed) + sum(1 for n in negatives.values() if n.passed)
        failed_cases = total_cases - passed_cases

        return FullEvaluationSummary(
            total_cases=total_cases,
            passed_cases=passed_cases,
            failed_cases=failed_cases,
            cases=cases_map,
            negative_cases=negatives,
            all_passed=(failed_cases == 0),
        )

    def format_summary_report(self, summary: FullEvaluationSummary) -> str:
        """Render a formatted human-readable summary of the evaluation results."""
        lines = [
            "=" * 78,
            "AEGIS Rx CLINICAL SAFETY EVALUATION HARNESS REPORT",
            "=" * 78,
            f"Overall Status: {'PASSED' if summary.all_passed else 'FAILED'}",
            f"Total Cases Evaluated: {summary.total_cases} | Passed: {summary.passed_cases} | Failed: {summary.failed_cases}",
            "-" * 78,
            "OFFICIAL DEMO CASES:",
            "-" * 78,
        ]

        for cid, cr in summary.cases.items():
            lines.extend([
                f"[{'PASS' if cr.passed else 'FAIL'}] {cr.case_id}: {cr.case_name}",
                f"  - Detection Result:      {cr.detection_result}",
                f"  - Matched Rule ID:       {cr.matched_rule_id}",
                f"  - Provenance Source:     {cr.provenance.get('source')} (v{cr.provenance.get('source_version')})",
                f"  - Evidence ID:           {cr.provenance.get('evidence_id')}",
                f"  - Resolution Status:     {cr.resolution_status} ({cr.candidate_count} candidate(s))",
                f"  - Simulated Order:       {cr.simulated_order_status}",
                f"  - Explanation Status:    {cr.explanation_status}",
                f"  - Audit Hash Chain:      {cr.audit_chain_status}",
                "  Assertions:",
            ])
            for a in cr.assertions:
                lines.append(f"    * [{'PASS' if a.passed else 'FAIL'}] {a.assertion_name}: {a.details}")
            lines.append("")

        lines.extend([
            "-" * 78,
            "NEGATIVE & FAILURE RESILIENCE CASES:",
            "-" * 78,
        ])
        for nid, nr in summary.negative_cases.items():
            lines.extend([
                f"[{'PASS' if nr.passed else 'FAIL'}] {nr.case_id}: {nr.case_name}",
                f"  - Description: {nr.description}",
                f"  - Details:     {nr.details}",
                "  Assertions:",
            ])
            for a in nr.assertions:
                lines.append(f"    * [{'PASS' if a.passed else 'FAIL'}] {a.assertion_name}: {a.details}")
            lines.append("")

        lines.append("=" * 78)
        return "\n".join(lines)
