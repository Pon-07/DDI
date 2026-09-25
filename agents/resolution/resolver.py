from typing import Any, Dict, List, Optional, Union

from agents.resolution.schemas import ResolutionCandidate, ResolutionResult
from agents.risk.schemas import Finding
from agents.rules.schemas import RulePack, SafetyRule


class SafetyResolutionEngine:
    """
    Safety Resolution Engine evaluating safety findings against validated rule/evidence contexts.
    Produces candidates ONLY when an explicit, validated clinical action exists.
    Never invents unvalidated doses, frequencies, or alternatives.
    Enforces mandatory human cosign (requires_cosign=True) for all actions.
    """

    def __init__(
        self,
        rule_context: Optional[
            Union[RulePack, List[SafetyRule], Dict[str, SafetyRule], SafetyRule]
        ] = None,
    ):
        self._rules_by_id: Dict[str, SafetyRule] = {}
        self._rules_by_pair: Dict[str, List[SafetyRule]] = {}
        if rule_context:
            self.load_rule_context(rule_context)

    def load_rule_context(
        self,
        rule_context: Union[RulePack, List[SafetyRule], Dict[str, SafetyRule], SafetyRule],
    ) -> None:
        """
        Load and index validated safety rules for resolution candidate matching.
        """
        rules: List[SafetyRule] = []
        if isinstance(rule_context, RulePack):
            rules = rule_context.rules
        elif isinstance(rule_context, list):
            rules = rule_context
        elif isinstance(rule_context, dict):
            rules = list(rule_context.values())
        elif isinstance(rule_context, SafetyRule):
            rules = [rule_context]

        for rule in rules:
            if not isinstance(rule, SafetyRule):
                continue
            if rule.status != "active":
                continue

            self._rules_by_id[rule.rule_id] = rule
            if rule.evidence_id:
                self._rules_by_id[rule.evidence_id] = rule

            if rule.drug_a and rule.drug_b:
                pair_key_1 = f"{rule.drug_a.lower().strip()}_{rule.drug_b.lower().strip()}"
                pair_key_2 = f"{rule.drug_b.lower().strip()}_{rule.drug_a.lower().strip()}"
                self._rules_by_pair.setdefault(pair_key_1, []).append(rule)
                self._rules_by_pair.setdefault(pair_key_2, []).append(rule)

    def _extract_finding_attr(self, finding: Any, attr: str, default: Any = None) -> Any:
        if isinstance(finding, dict):
            return finding.get(attr, default)
        return getattr(finding, attr, default)

    def _find_matching_validated_rule(self, finding: Any) -> Optional[SafetyRule]:
        """
        Search loaded validated rules for a match with finding's rule_id, evidence_id, or drug pair.
        """
        rule_id = self._extract_finding_attr(finding, "rule_id")
        if rule_id and str(rule_id) in self._rules_by_id:
            return self._rules_by_id[str(rule_id)]

        trace = self._extract_finding_attr(finding, "trace", {}) or {}
        if isinstance(trace, dict):
            ev_id = trace.get("evidence_id")
            if ev_id and str(ev_id) in self._rules_by_id:
                return self._rules_by_id[str(ev_id)]

            matched_pair = trace.get("matched_pair")
            if matched_pair and isinstance(matched_pair, list) and len(matched_pair) == 2:
                pair_key = f"{str(matched_pair[0]).lower().strip()}_{str(matched_pair[1]).lower().strip()}"
                if pair_key in self._rules_by_pair and self._rules_by_pair[pair_key]:
                    return self._rules_by_pair[pair_key][0]

        inputs = self._extract_finding_attr(finding, "inputs", {}) or {}
        if isinstance(inputs, dict):
            drug_a = inputs.get("drug_a")
            drug_b = inputs.get("drug_b")
            if drug_a and drug_b:
                pair_key = f"{str(drug_a).lower().strip()}_{str(drug_b).lower().strip()}"
                if pair_key in self._rules_by_pair and self._rules_by_pair[pair_key]:
                    return self._rules_by_pair[pair_key][0]

        return None

    def resolve_finding(
        self,
        finding: Union[Finding, Dict[str, Any], Any],
        rule_context: Optional[
            Union[RulePack, List[SafetyRule], Dict[str, SafetyRule], SafetyRule]
        ] = None,
    ) -> ResolutionResult:
        """
        Evaluate a single safety finding and produce resolution candidates ONLY if
        an explicit, validated action is present in the rule/finding evidence.
        """
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

        if matched_rule and matched_rule.action and matched_rule.action.strip():
            validated_action = matched_rule.action.strip()
            action_source = matched_rule.source
            action_evidence_id = matched_rule.evidence_id or matched_rule.rule_id
            action_type = matched_rule.rule_type or "validated_guidance"
        else:
            # 2. From finding's own validated action field
            finding_action = self._extract_finding_attr(finding, "action")
            if finding_action and str(finding_action).strip() and str(finding_action).lower() != "none":
                validated_action = str(finding_action).strip()

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
        rule_context: Optional[
            Union[RulePack, List[SafetyRule], Dict[str, SafetyRule], SafetyRule]
        ] = None,
    ) -> List[ResolutionResult]:
        """
        Evaluate a collection of safety findings against validated rule contexts.
        """
        if rule_context:
            self.load_rule_context(rule_context)

        return [self.resolve_finding(f) for f in findings]
