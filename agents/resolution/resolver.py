import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from agents.resolution.schemas import (
    ResolutionCandidate,
    ResolutionPipelineResult,
    ResolutionResult,
    SimulatedOrder,
)
from agents.risk.schemas import Finding
from agents.rules.schemas import RulePack, SafetyRule as SafetyRuleSchema

DEMO_PACK_PATH = Path(__file__).resolve().parent.parent / "rules" / "packs" / "aegis_hackathon_demo_v1.json"


class SafetyResolutionEngine:
    """
    Safety Resolution Engine evaluating safety findings against validated rule/evidence contexts.
    Produces candidates ONLY when an explicit, validated clinical action exists.
    Never invents unvalidated doses, frequencies, or alternatives.
    Enforces mandatory human cosign (requires_cosign=True) for all actions.

    Flow:
    Finding -> deterministic resolution -> safety-filtered candidate actions -> ranked candidates -> cosign-ready simulated order.
    """

    SEVERITY_WEIGHTS: Dict[str, float] = {
        "critical": 100.0,
        "contraindicated": 95.0,
        "major": 80.0,
        "review_required": 75.0,
        "high": 70.0,
        "moderate": 50.0,
        "minor": 30.0,
        "low": 20.0,
        "undetermined": 10.0,
        "unspecified": 10.0,
    }

    def __init__(
        self,
        rule_context: Optional[Any] = None,
        explicator: Optional[Any] = None,
        audit_ledger: Optional[Any] = None,
    ):
        self._rules_by_id: Dict[str, Any] = {}
        self._rules_by_pair: Dict[str, List[Any]] = {}
        self._rules_by_drug: Dict[str, List[Any]] = {}
        self.explicator = explicator
        self.audit_ledger = audit_ledger
        if rule_context:
            self.load_rule_context(rule_context)
        elif DEMO_PACK_PATH.exists():
            try:
                with open(DEMO_PACK_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.load_rule_context(data.get("rules", []))
            except Exception:
                pass

    def load_rule_context(
        self,
        rule_context: Any,
    ) -> None:
        """
        Load and index validated safety rules for resolution candidate matching.
        Supports RulePack, RulePackService, List[SafetyRule], Dict, or single SafetyRule.
        Strictly filters out any inactive/disabled/draft/deprecated rules.
        """
        rules: List[Any] = []
        if hasattr(rule_context, "get_active_rules") and callable(rule_context.get_active_rules):
            rules = rule_context.get_active_rules()
        elif isinstance(rule_context, RulePack):
            rules = rule_context.rules
        elif isinstance(rule_context, list):
            rules = rule_context
        elif isinstance(rule_context, dict):
            rules = list(rule_context.values())
        elif rule_context is not None:
            rules = [rule_context]

        for rule in rules:
            if not rule:
                continue

            rule_id = getattr(rule, "rule_id", None) or (rule.get("rule_id") if isinstance(rule, dict) else None)
            if not rule_id:
                continue

            status = getattr(rule, "status", None) or (rule.get("status") if isinstance(rule, dict) else "active")
            if not status or str(status).lower().strip() != "active":
                continue

            self._rules_by_id[str(rule_id)] = rule
            evidence_id = getattr(rule, "evidence_id", None) or (rule.get("evidence_id") if isinstance(rule, dict) else None)
            if evidence_id:
                self._rules_by_id[str(evidence_id)] = rule

            drug_a = getattr(rule, "drug_a", None) or (rule.get("drug_a") if isinstance(rule, dict) else None)
            drug_b = getattr(rule, "drug_b", None) or (rule.get("drug_b") if isinstance(rule, dict) else None)

            if drug_a and drug_b:
                pair_key_1 = f"{str(drug_a).lower().strip()}_{str(drug_b).lower().strip()}"
                pair_key_2 = f"{str(drug_b).lower().strip()}_{str(drug_a).lower().strip()}"
                self._rules_by_pair.setdefault(pair_key_1, []).append(rule)
                self._rules_by_pair.setdefault(pair_key_2, []).append(rule)
            elif drug_a and not drug_b:
                single_key = str(drug_a).lower().strip()
                self._rules_by_drug.setdefault(single_key, []).append(rule)

    def _extract_finding_attr(self, finding: Any, attr: str, default: Any = None) -> Any:
        if finding is None:
            return default
        if isinstance(finding, dict):
            val = finding.get(attr, default)
        else:
            val = getattr(finding, attr, default)

        if val is None or (isinstance(val, str) and not val.strip()):
            # Look inside trace if not found as direct attribute
            if attr in ("source", "evidence_id", "rule_id", "severity"):
                trace = None
                if isinstance(finding, dict):
                    trace = finding.get("trace")
                else:
                    trace = getattr(finding, "trace", None)
                if isinstance(trace, str):
                    try:
                        trace = json.loads(trace)
                    except Exception:
                        trace = None
                if isinstance(trace, dict) and attr in trace:
                    val = trace.get(attr)

        if attr in ("inputs", "trace") and isinstance(val, str):
            try:
                val = json.loads(val)
            except Exception:
                pass
        return val if val is not None else default

    def _find_matching_validated_rule(self, finding: Any) -> Optional[Any]:
        """
        Search loaded validated rules for a match with finding's rule_id, evidence_id, drug pair, or single drug.
        Enforces that any matched rule must be in 'active' status.
        """
        if finding is None:
            return None

        rule_id = self._extract_finding_attr(finding, "rule_id")
        if rule_id and str(rule_id) in self._rules_by_id:
            rule = self._rules_by_id[str(rule_id)]
            st = getattr(rule, "status", None) or (rule.get("status") if isinstance(rule, dict) else "active")
            if str(st).lower().strip() == "active":
                return rule

        trace = self._extract_finding_attr(finding, "trace", {}) or {}
        if isinstance(trace, dict):
            ev_id = trace.get("evidence_id")
            if ev_id and str(ev_id) in self._rules_by_id:
                rule = self._rules_by_id[str(ev_id)]
                st = getattr(rule, "status", None) or (rule.get("status") if isinstance(rule, dict) else "active")
                if str(st).lower().strip() == "active":
                    return rule

            tr_rule_id = trace.get("rule_id")
            if tr_rule_id and str(tr_rule_id) in self._rules_by_id:
                rule = self._rules_by_id[str(tr_rule_id)]
                st = getattr(rule, "status", None) or (rule.get("status") if isinstance(rule, dict) else "active")
                if str(st).lower().strip() == "active":
                    return rule

            matched_pair = trace.get("matched_pair")
            if matched_pair and isinstance(matched_pair, list):
                if len(matched_pair) == 2:
                    pair_key = f"{str(matched_pair[0]).lower().strip()}_{str(matched_pair[1]).lower().strip()}"
                    if pair_key in self._rules_by_pair and self._rules_by_pair[pair_key]:
                        return self._rules_by_pair[pair_key][0]
                elif len(matched_pair) == 1:
                    single_key = str(matched_pair[0]).lower().strip()
                    if single_key in self._rules_by_drug and self._rules_by_drug[single_key]:
                        return self._rules_by_drug[single_key][0]

        inputs = self._extract_finding_attr(finding, "inputs", {}) or {}
        if isinstance(inputs, dict):
            drug_a = inputs.get("drug_a")
            drug_b = inputs.get("drug_b")
            if drug_a and drug_b:
                pair_key = f"{str(drug_a).lower().strip()}_{str(drug_b).lower().strip()}"
                if pair_key in self._rules_by_pair and self._rules_by_pair[pair_key]:
                    return self._rules_by_pair[pair_key][0]
            if drug_a and str(drug_a).lower().strip() in self._rules_by_drug:
                return self._rules_by_drug[str(drug_a).lower().strip()][0]
            if drug_b and str(drug_b).lower().strip() in self._rules_by_drug:
                return self._rules_by_drug[str(drug_b).lower().strip()][0]

        return None

    def resolve_finding(
        self,
        finding: Union[Finding, Dict[str, Any], Any],
        rule_context: Optional[Any] = None,
    ) -> ResolutionResult:
        """
        Evaluate a single safety finding and produce resolution candidates ONLY if
        an explicit, validated action is present in the rule/finding evidence.
        """
        if finding is None:
            return ResolutionResult(
                finding_id="UNKNOWN",
                status="requires_human_review",
                candidates=[],
                review_reason="No finding data provided. Mandatory clinical review required.",
            )

        if rule_context:
            self.load_rule_context(rule_context)

        # Extract finding ID / rule ID
        finding_id = (
            self._extract_finding_attr(finding, "id")
            or self._extract_finding_attr(finding, "rule_id")
            or "UNKNOWN_FINDING"
        )

        trace = self._extract_finding_attr(finding, "trace", {}) or {}
        source = self._extract_finding_attr(finding, "source") or "Unknown"
        evidence_id = (
            (trace.get("evidence_id") if isinstance(trace, dict) else None)
            or self._extract_finding_attr(finding, "evidence_id")
            or self._extract_finding_attr(finding, "rule_id")
        )

        # Check for explicit validated action:
        # 1. From an attached validated rule in context
        matched_rule = self._find_matching_validated_rule(finding)
        validated_action = None
        action_source = source
        action_evidence_id = evidence_id
        action_type = "validated_guidance"

        if matched_rule:
            rule_act = getattr(matched_rule, "action", None) or (
                matched_rule.get("action") if isinstance(matched_rule, dict) else None
            )
            if rule_act and str(rule_act).strip():
                clean_act = str(rule_act).strip()
                if clean_act.lower() not in {"none", "null", "n/a", "no action", "unspecified", "unknown"}:
                    validated_action = clean_act
                    action_source = getattr(matched_rule, "source", None) or (
                        matched_rule.get("source") if isinstance(matched_rule, dict) else source
                    )
                    action_evidence_id = (
                        getattr(matched_rule, "evidence_id", None)
                        or getattr(matched_rule, "rule_id", None)
                        or (matched_rule.get("evidence_id") if isinstance(matched_rule, dict) else None)
                        or (matched_rule.get("rule_id") if isinstance(matched_rule, dict) else None)
                    )
                    action_type = (
                        getattr(matched_rule, "rule_type", None)
                        or (matched_rule.get("rule_type") if isinstance(matched_rule, dict) else None)
                        or "validated_guidance"
                    )

        if not validated_action:
            # 2. From finding's own validated action field
            finding_action = self._extract_finding_attr(finding, "action")
            if finding_action and str(finding_action).strip():
                clean_f_act = str(finding_action).strip()
                if clean_f_act.lower() not in {"none", "null", "n/a", "no action", "unspecified", "unknown"}:
                    validated_action = clean_f_act

        # If NO explicit validated action exists, human clinical review is strictly required
        if not validated_action:
            return ResolutionResult(
                finding_id=finding_id,
                status="requires_human_review",
                candidates=[],
                review_reason=(
                    "No explicit validated clinical action is present in the knowledge source. "
                    "Mandatory clinical review by a licensed healthcare provider is required."
                ),
            )

        # Check if critical clinical data is missing (never guess or auto-prescribe)
        inputs = self._extract_finding_attr(finding, "inputs", {}) or {}
        data_needed = inputs.get("data_needed") if isinstance(inputs, dict) else None
        if data_needed:
            needed_items = list(data_needed) if isinstance(data_needed, (list, tuple, set)) else [str(data_needed)]
            needed_str = ", ".join(str(d) for d in needed_items)

            candidate = ResolutionCandidate(
                finding_id=finding_id,
                action_type="missing_data_review",
                description=f"Clinical review required: missing required parameters ({needed_str}). Order baseline labs before proceeding.",
                rationale=f"Missing clinical data: {needed_str}. Clinical review required.",
                source=action_source,
                evidence_id=str(action_evidence_id) if action_evidence_id else None,
                requires_cosign=True,
            )
            return ResolutionResult(
                finding_id=finding_id,
                status="requires_human_review",
                candidates=[candidate],
                review_reason=f"Missing clinical data needed: {needed_str}. Mandatory clinician review required.",
            )

        # Create candidate with preserved provenance and mandatory cosign
        finding_desc = self._extract_finding_attr(finding, "description") or "Validated safety finding"
        rationale = (
            f"Validated action from {action_source} (Evidence ID: {action_evidence_id}). "
            f"Finding: {finding_desc}"
        )

        candidate = ResolutionCandidate(
            finding_id=finding_id,
            action_type=action_type,
            description=validated_action,
            rationale=rationale,
            source=action_source,
            evidence_id=str(action_evidence_id) if action_evidence_id else None,
            requires_cosign=True,
        )

        return ResolutionResult(
            finding_id=finding_id,
            status="actionable",
            candidates=[candidate],
            review_reason=None,
        )

    def resolve_findings(
        self,
        findings: List[Union[Finding, Dict[str, Any], Any]],
        rule_context: Optional[Any] = None,
    ) -> List[ResolutionResult]:
        """
        Evaluate a collection of safety findings against validated rule contexts.
        """
        if rule_context:
            self.load_rule_context(rule_context)

        return [self.resolve_finding(f) for f in findings]

    def filter_candidate_actions(
        self,
        candidates: List[ResolutionCandidate],
    ) -> List[ResolutionCandidate]:
        """
        Filter candidate actions against strict safety constraints:
        - Mandatory human cosign (requires_cosign must be True).
        - Non-empty description and source.
        - Rejection of placeholder or non-action text.
        - Deterministic deduplication.
        """
        filtered: List[ResolutionCandidate] = []
        seen_keys: Set[Tuple[str, str, str]] = set()

        for c in candidates:
            # Enforce requires_cosign
            if not getattr(c, "requires_cosign", False):
                continue

            desc = (c.description or "").strip()
            if not desc or desc.lower() in {"none", "null", "n/a", "no action", "unknown", "unspecified"}:
                continue

            source = (c.source or "").strip()
            if not source or source.lower() == "unknown":
                continue

            dedup_key = (
                str(c.finding_id),
                c.action_type.lower().strip(),
                desc.lower(),
            )
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            filtered.append(c)

        return filtered

    def _score_candidate(
        self,
        candidate: ResolutionCandidate,
        finding_severity: Optional[str] = None,
    ) -> float:
        """
        Compute a deterministic priority score for a candidate action based on:
        1. Clinical severity of the finding
        2. Action urgency keywords (avoid, discontinue, hold > adjust, evaluate > monitor, review)
        3. Evidence provenance strength
        """
        score = 0.0

        # 1. Severity weight
        sev_key = (finding_severity or "").lower().strip()
        score += self.SEVERITY_WEIGHTS.get(sev_key, 10.0)

        # 2. Action urgency
        desc_lower = (candidate.description or "").lower()
        action_type_lower = (candidate.action_type or "").lower()

        if any(kw in desc_lower or kw in action_type_lower for kw in ["avoid", "discontinue", "stop", "contraindicated", "contraindication", "hold"]):
            score += 30.0
        elif any(kw in desc_lower or kw in action_type_lower for kw in ["adjust", "adjustment", "modify", "alternative", "safer alternative"]):
            score += 20.0
        elif any(kw in desc_lower or kw in action_type_lower for kw in ["monitor", "serum", "renal", "lab", "review", "consult"]):
            score += 10.0
        else:
            score += 5.0

        # 3. Provenance strength
        if candidate.evidence_id:
            score += 10.0
        if candidate.source and candidate.source.lower() not in {"unknown", "unspecified"}:
            score += 5.0

        return score

    def rank_candidates(
        self,
        candidates: List[ResolutionCandidate],
        finding_severity: Optional[str] = None,
    ) -> List[ResolutionCandidate]:
        """
        Deterministically rank candidate actions by clinical urgency and rule specificity.
        Never relies on non-deterministic heuristics or arbitrary ordering.
        """
        if not candidates:
            return []

        scored_candidates: List[Tuple[float, ResolutionCandidate]] = []
        for c in candidates:
            score = self._score_candidate(c, finding_severity=finding_severity)
            updated_candidate = c.model_copy(update={"priority_score": score})
            scored_candidates.append((score, updated_candidate))

        # Sort descending by score, tie-break deterministically by finding_id, action_type, description
        scored_candidates.sort(
            key=lambda item: (
                -item[0],
                str(item[1].finding_id),
                item[1].action_type,
                item[1].description,
            )
        )
        return [item[1] for item in scored_candidates]

    def create_simulated_order(
        self,
        candidate: ResolutionCandidate,
        finding: Optional[Any] = None,
        existing_medication: Optional[Any] = None,
        patient_id: Optional[Union[int, str]] = None,
    ) -> SimulatedOrder:
        """
        Transform a top-ranked validated candidate action into a cosign-ready simulated order.
        Does not auto-execute or alter prescriptions autonomously.
        Never invents clinical doses, thresholds, or treatment values.
        Preserves existing medication details if present; otherwise leaves them None.
        """
        # 1. Resolve patient_id safely
        resolved_patient_id = patient_id
        if resolved_patient_id is None and existing_medication is not None:
            resolved_patient_id = (
                existing_medication.get("patient_id")
                if isinstance(existing_medication, dict)
                else getattr(existing_medication, "patient_id", None)
            )
        if resolved_patient_id is None and finding is not None:
            inputs = self._extract_finding_attr(finding, "inputs", {}) or {}
            if isinstance(inputs, dict):
                resolved_patient_id = inputs.get("patient_id")
            if resolved_patient_id is None:
                resolved_patient_id = self._extract_finding_attr(finding, "patient_id")

        # 2. Resolve target drug name safely
        drug_name = None
        if existing_medication is not None:
            drug_name = (
                existing_medication.get("drug_name") or existing_medication.get("name")
                if isinstance(existing_medication, dict)
                else getattr(existing_medication, "drug_name", None) or getattr(existing_medication, "name", None)
            )
        if not drug_name and finding is not None:
            inputs = self._extract_finding_attr(finding, "inputs", {}) or {}
            if isinstance(inputs, dict):
                drug_name = inputs.get("drug_a") or inputs.get("drug_b")
            if not drug_name:
                trace = self._extract_finding_attr(finding, "trace", {}) or {}
                if isinstance(trace, dict):
                    matched_pair = trace.get("matched_pair")
                    if matched_pair and isinstance(matched_pair, list) and len(matched_pair) > 0:
                        drug_name = str(matched_pair[0])

        if not drug_name:
            drug_name = f"Medication-for-{candidate.finding_id}"

        # 3. Preserve existing order / medication details WITHOUT inventing anything
        medication_id = None
        dose = None
        dose_unit = None
        route = None
        frequency = None

        if existing_medication is not None:
            if isinstance(existing_medication, dict):
                medication_id = existing_medication.get("id") or existing_medication.get("medication_id")
                dose = existing_medication.get("dose")
                dose_unit = existing_medication.get("dose_unit")
                route = existing_medication.get("route")
                frequency = existing_medication.get("frequency")
            else:
                medication_id = getattr(existing_medication, "id", None) or getattr(existing_medication, "medication_id", None)
                dose = getattr(existing_medication, "dose", None)
                dose_unit = getattr(existing_medication, "dose_unit", None)
                route = getattr(existing_medication, "route", None)
                frequency = getattr(existing_medication, "frequency", None)

        # 4. Generate deterministic simulation_id
        raw_key = f"{candidate.finding_id}_{candidate.action_type}_{candidate.description[:20]}"
        hash_suffix = abs(hash(raw_key)) % 1000000
        sim_id = f"SIM-ORD-{candidate.finding_id}-{hash_suffix:06d}"

        return SimulatedOrder(
            simulation_id=sim_id,
            finding_id=candidate.finding_id,
            patient_id=resolved_patient_id,
            medication_id=medication_id,
            drug_name=str(drug_name),
            proposed_action=candidate.description,
            action_type=candidate.action_type,
            dose=str(dose) if dose is not None else None,
            dose_unit=str(dose_unit) if dose_unit is not None else None,
            route=str(route) if route is not None else None,
            frequency=str(frequency) if frequency is not None else None,
            status="pending_cosign",
            requires_cosign=True,
            auto_execute=False,
            is_simulated=True,
            rationale=candidate.rationale,
            source=candidate.source,
            evidence_id=candidate.evidence_id,
        )

    def _prepare_finding_for_explication(
        self,
        finding: Any,
        res_result: ResolutionResult,
        ranked_candidates: List[ResolutionCandidate],
    ) -> Dict[str, Any]:
        """
        Enrich finding dictionary for ExplicatorService with matched rule evidence and guidance.
        """
        inputs = self._extract_finding_attr(finding, "inputs", {}) or {}
        trace = self._extract_finding_attr(finding, "trace", {}) or {}

        action = self._extract_finding_attr(finding, "action")
        evidence_id = (
            (trace.get("evidence_id") if isinstance(trace, dict) else None)
            or self._extract_finding_attr(finding, "evidence_id")
        )
        source = self._extract_finding_attr(finding, "source") or "Unknown"

        if ranked_candidates and res_result.status == "actionable":
            top_cand = ranked_candidates[0]
            if not action:
                action = top_cand.description
            if top_cand.evidence_id:
                evidence_id = top_cand.evidence_id
            if top_cand.source:
                source = top_cand.source

        return {
            "id": self._extract_finding_attr(finding, "id"),
            "rule_id": self._extract_finding_attr(finding, "rule_id") or "UNKNOWN_RULE",
            "severity": self._extract_finding_attr(finding, "severity") or "undetermined",
            "title": self._extract_finding_attr(finding, "title") or "Medication Safety Finding",
            "description": self._extract_finding_attr(finding, "description") or "Reported safety finding.",
            "action": action,
            "inputs": inputs,
            "trace": trace,
            "source": source,
            "evidence_id": evidence_id,
        }

    def resolve_finding_pipeline(
        self,
        finding: Union[Finding, Dict[str, Any], Any],
        rule_context: Optional[Any] = None,
        existing_medication: Optional[Any] = None,
        patient_id: Optional[Union[int, str]] = None,
    ) -> ResolutionPipelineResult:
        """
        Execute the complete deterministic safety resolution pipeline:
        Finding -> deterministic resolution -> safety-filtered candidate actions -> ranked candidates -> cosign-ready simulated order.
        Optionally generates explanation and records immutable events in audit ledger.
        """
        finding_id = (
            self._extract_finding_attr(finding, "id")
            or self._extract_finding_attr(finding, "rule_id")
            or "UNKNOWN_FINDING"
        )
        severity = self._extract_finding_attr(finding, "severity") or "undetermined"

        # 1. Deterministic resolution
        res_result = self.resolve_finding(finding, rule_context=rule_context)
        raw_candidates = res_result.candidates

        # 2. Safety-filtered candidate actions
        filtered_candidates = self.filter_candidate_actions(raw_candidates)

        # 3. Ranked candidates
        ranked_candidates = self.rank_candidates(filtered_candidates, finding_severity=severity)

        # 4. Cosign-ready simulated order
        simulated_order = None
        if ranked_candidates and res_result.status == "actionable":
            simulated_order = self.create_simulated_order(
                candidate=ranked_candidates[0],
                finding=finding,
                existing_medication=existing_medication,
                patient_id=patient_id,
            )

        # 5. Optional Explicator integration
        explanation = None
        if self.explicator is not None:
            enriched_finding = self._prepare_finding_for_explication(finding, res_result, ranked_candidates)
            explanation = self.explicator.explicate(enriched_finding)

        # 6. Optional Audit Ledger integration
        if self.audit_ledger is not None:
            # Audit resolution evaluation
            self.audit_ledger.append_event(
                actor="safety_resolution_engine",
                event_type="SAFETY_RESOLUTION_EVALUATED",
                payload={
                    "finding_id": finding_id,
                    "severity": severity,
                    "status": res_result.status,
                    "candidates_count": len(ranked_candidates),
                    "requires_cosign": True,
                    "top_action": ranked_candidates[0].description if ranked_candidates else None,
                },
            )

            # Audit explanation if generated
            if explanation:
                self.audit_ledger.append_event(
                    actor="explicator_service",
                    event_type="EXPLANATION_GENERATED",
                    payload={
                        "finding_id": finding_id,
                        "evidence_source": explanation.evidence_source,
                        "evidence_id": explanation.evidence_id,
                        "is_fallback": explanation.is_fallback,
                        "is_llm_enhanced": getattr(explanation, "is_llm_enhanced", False),
                        "human_action_status": explanation.human_action_status,
                        "summary": explanation.summary,
                    },
                )

            # Audit simulated order if generated
            if simulated_order:
                self.audit_ledger.append_event(
                    actor="safety_resolution_engine",
                    event_type="SIMULATED_ORDER_CREATED",
                    payload={
                        "simulation_id": simulated_order.simulation_id,
                        "finding_id": simulated_order.finding_id,
                        "patient_id": simulated_order.patient_id,
                        "medication_id": simulated_order.medication_id,
                        "drug_name": simulated_order.drug_name,
                        "proposed_action": simulated_order.proposed_action,
                        "action_type": simulated_order.action_type,
                        "status": simulated_order.status,
                        "requires_cosign": simulated_order.requires_cosign,
                        "auto_execute": simulated_order.auto_execute,
                        "is_simulated": simulated_order.is_simulated,
                    },
                )

        return ResolutionPipelineResult(
            finding_id=finding_id,
            finding_severity=severity,
            status=res_result.status,
            raw_candidates=raw_candidates,
            safety_filtered_candidates=filtered_candidates,
            ranked_candidates=ranked_candidates,
            simulated_order=simulated_order,
            explanation=explanation,
            requires_cosign=True,
            review_reason=res_result.review_reason,
        )

    def resolve_findings_pipeline(
        self,
        findings: List[Union[Finding, Dict[str, Any], Any]],
        rule_context: Optional[Any] = None,
        existing_medications: Optional[List[Any]] = None,
    ) -> List[ResolutionPipelineResult]:
        """
        Execute the pipeline for a collection of findings.
        Includes deterministic deduplication of findings.
        """
        if rule_context:
            self.load_rule_context(rule_context)

        # Index existing medications by lowercase name if available
        meds_by_name: Dict[str, Any] = {}
        if existing_medications:
            for m in existing_medications:
                name = None
                if isinstance(m, dict):
                    name = m.get("drug_name") or m.get("name")
                else:
                    name = getattr(m, "drug_name", None) or getattr(m, "name", None)
                if name:
                    meds_by_name[str(name).lower().strip()] = m

        # Deduplicate findings deterministically
        seen_finding_signatures: Set[Tuple[str, str, Tuple[str, ...]]] = set()
        deduped_findings: List[Any] = []
        for f in findings:
            f_id = str(self._extract_finding_attr(f, "id") or self._extract_finding_attr(f, "rule_id") or "")
            r_id = str(self._extract_finding_attr(f, "rule_id") or "")
            trace = self._extract_finding_attr(f, "trace", {}) or {}
            matched_pair: Tuple[str, ...] = ()
            if isinstance(trace, dict) and "matched_pair" in trace:
                mp = trace["matched_pair"]
                if isinstance(mp, list):
                    matched_pair = tuple(sorted(str(d).lower().strip() for d in mp))
            sig = (f_id, r_id, matched_pair)
            if sig in seen_finding_signatures:
                continue
            seen_finding_signatures.add(sig)
            deduped_findings.append(f)

        results: List[ResolutionPipelineResult] = []
        for f in deduped_findings:
            # Attempt to associate matching medication
            matched_med = None
            inputs = self._extract_finding_attr(f, "inputs", {}) or {}
            if isinstance(inputs, dict):
                drug_a = inputs.get("drug_a")
                drug_b = inputs.get("drug_b")
                if drug_a and str(drug_a).lower().strip() in meds_by_name:
                    matched_med = meds_by_name[str(drug_a).lower().strip()]
                elif drug_b and str(drug_b).lower().strip() in meds_by_name:
                    matched_med = meds_by_name[str(drug_b).lower().strip()]

            res = self.resolve_finding_pipeline(
                finding=f,
                existing_medication=matched_med,
            )
            results.append(res)

        return results
