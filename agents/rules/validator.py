from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from pydantic import ValidationError

from agents.rules.schemas import SafetyRule


@dataclass
class RuleValidationIssue:
    """Represents a specific validation defect identified on a safety rule."""

    rule_id: Optional[str]
    field: str
    message: str


@dataclass
class RulePackValidationResult:
    """Summary and details of a rule pack validation run."""

    is_valid: bool
    total_rules: int
    valid_rules_count: int
    invalid_rules_count: int
    errors: List[RuleValidationIssue] = field(default_factory=list)
    validated_rules: List[SafetyRule] = field(default_factory=list)


class RulePackValidator:
    """
    Validator ensuring non-inferential integrity, provenance tracking,
    and schema correctness for medication safety rules and rule packs.
    """

    ALLOWED_STATUSES: Set[str] = {
        "active",
        "draft",
        "deprecated",
        "archived",
    }

    def validate_rule(
        self,
        rule_data: Union[SafetyRule, Dict[str, Any]],
    ) -> Tuple[bool, Optional[SafetyRule], List[RuleValidationIssue]]:
        """
        Validate a single safety rule against schema and provenance requirements.
        """
        issues: List[RuleValidationIssue] = []

        if isinstance(rule_data, dict):
            rule_id = rule_data.get("rule_id")
            try:
                rule = SafetyRule(**rule_data)
            except ValidationError as exc:
                for err in exc.errors():
                    field_name = ".".join(str(loc) for loc in err["loc"])
                    issues.append(
                        RuleValidationIssue(
                            rule_id=str(rule_id) if rule_id else None,
                            field=field_name,
                            message=err["msg"],
                        )
                    )
                return False, None, issues
        elif isinstance(rule_data, SafetyRule):
            rule = rule_data
            rule_id = rule.rule_id
        else:
            return False, None, [
                RuleValidationIssue(
                    rule_id=None,
                    field="type",
                    message=f"Expected SafetyRule or dict, got {type(rule_data).__name__}",
                )
            ]

        # Domain checks
        if not rule.source or not rule.source.strip():
            issues.append(
                RuleValidationIssue(
                    rule_id=rule.rule_id,
                    field="source",
                    message="Source provenance must not be empty.",
                )
            )

        if not rule.source_version or not rule.source_version.strip():
            issues.append(
                RuleValidationIssue(
                    rule_id=rule.rule_id,
                    field="source_version",
                    message="Source version must be specified.",
                )
            )

        if not rule.evidence_id or not rule.evidence_id.strip():
            issues.append(
                RuleValidationIssue(
                    rule_id=rule.rule_id,
                    field="evidence_id",
                    message="Evidence ID is mandatory to maintain data provenance.",
                )
            )

        if not rule.evidence_text or not rule.evidence_text.strip():
            issues.append(
                RuleValidationIssue(
                    rule_id=rule.rule_id,
                    field="evidence_text",
                    message="Evidence text cannot be blank.",
                )
            )

        if rule.status.lower().strip() not in self.ALLOWED_STATUSES:
            issues.append(
                RuleValidationIssue(
                    rule_id=rule.rule_id,
                    field="status",
                    message=f"Invalid rule status '{rule.status}'. Allowed: {sorted(list(self.ALLOWED_STATUSES))}",
                )
            )

        is_valid = len(issues) == 0
        return is_valid, (rule if is_valid else None), issues

    def validate_pack(
        self,
        rules: List[Union[SafetyRule, Dict[str, Any]]],
    ) -> RulePackValidationResult:
        """
        Validate a collection of rules, enforcing rule_id uniqueness, provenance, and validity.
        """
        seen_rule_ids: Set[str] = set()
        errors: List[RuleValidationIssue] = []
        validated_rules: List[SafetyRule] = []

        for item in rules:
            is_valid, rule_obj, issues = self.validate_rule(item)
            if not is_valid or rule_obj is None:
                errors.extend(issues)
                continue

            rule_id_clean = rule_obj.rule_id.strip()
            if rule_id_clean in seen_rule_ids:
                errors.append(
                    RuleValidationIssue(
                        rule_id=rule_id_clean,
                        field="rule_id",
                        message=f"Duplicate rule_id '{rule_id_clean}' found in rule pack.",
                    )
                )
                continue

            seen_rule_ids.add(rule_id_clean)
            validated_rules.append(rule_obj)

        total = len(rules)
        valid_count = len(validated_rules)
        invalid_count = total - valid_count

        return RulePackValidationResult(
            is_valid=(len(errors) == 0),
            total_rules=total,
            valid_rules_count=valid_count,
            invalid_rules_count=invalid_count,
            errors=errors,
            validated_rules=validated_rules,
        )
