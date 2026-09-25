"""Deterministic, versioned safety rule-pack persistence.

Reuses the existing SQLAlchemy SafetyRule and RuleVersion models.
Does not infer clinical thresholds or generate rules with an LLM.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from sqlalchemy.orm import Session

from agents.rules.schemas import SafetyRule as SafetyRuleSchema
from agents.rules.validator import RulePackValidator, RuleValidationIssue
from models.models import RuleVersion, SafetyRule

PACKS_DIR = Path(__file__).resolve().parent / "packs"
DEMO_RULE_PACK_PATH = PACKS_DIR / "aegis_hackathon_demo_v1.json"

CANONICAL_RULE_FIELDS = (
    "rule_id",
    "rule_type",
    "category",
    "drug_a",
    "drug_b",
    "source",
    "source_version",
    "evidence_id",
    "evidence_text",
    "severity",
    "action",
    "status",
)

REQUIRED_PROVENANCE_FIELDS = (
    "rule_id",
    "rule_type",
    "category",
    "source",
    "source_version",
    "evidence_id",
    "evidence_text",
    "severity",
    "action",
)

ALLOWED_RULE_TYPES = frozenset(
    {
        "drug_interaction",
        "renal_risk",
        "duplicate_therapy",
    }
)

ALLOWED_CATEGORIES = frozenset(
    {
        "drug_drug_interaction",
        "renal_risk",
        "duplicate_therapy",
    }
)


class RulePackError(ValueError):
    """Base error for rule-pack operations."""


class RulePackValidationError(RulePackError):
    """Raised when a pack or rule fails structural/provenance validation."""

    def __init__(self, message: str, issues: Optional[List[RuleValidationIssue]] = None):
        super().__init__(message)
        self.issues = issues or []


class RulePackVersionExistsError(RulePackError):
    """Raised when a pack_name + version already exists (no silent overwrite)."""


@dataclass(frozen=True)
class HashVerificationResult:
    matches: bool
    stored_hash: str
    computed_hash: str


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _as_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def canonicalize_rule(rule: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a stable, hashable representation of one rule's content."""
    canonical: Dict[str, Any] = {}
    for field in CANONICAL_RULE_FIELDS:
        value = rule.get(field)
        if field == "status" and _is_blank(value):
            canonical[field] = "active"
            continue
        if isinstance(value, str):
            canonical[field] = value.strip()
        elif value is None:
            canonical[field] = None
        else:
            canonical[field] = value
    return canonical


def canonicalize_rule_content(rules: Sequence[Mapping[str, Any]]) -> str:
    """Canonical JSON for rule content only (order-independent)."""
    normalized = [canonicalize_rule(rule) for rule in rules]
    normalized.sort(key=lambda item: str(item.get("rule_id") or ""))
    payload = {"rules": normalized}
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def compute_content_hash(rules: Sequence[Mapping[str, Any]]) -> str:
    """SHA-256 of canonicalized rule content."""
    canonical = canonicalize_rule_content(rules)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_rule_pack_file(path: Union[str, Path]) -> Dict[str, Any]:
    pack_path = Path(path)
    with pack_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise RulePackValidationError("Rule pack file must contain a JSON object.")
    return data


def rule_to_mapping(rule: Union[Mapping[str, Any], SafetyRule, SafetyRuleSchema]) -> Dict[str, Any]:
    if isinstance(rule, SafetyRule):
        return {
            "rule_id": rule.rule_id,
            "rule_type": rule.rule_type,
            "category": rule.category,
            "drug_a": rule.drug_a,
            "drug_b": rule.drug_b,
            "source": rule.source,
            "source_version": rule.source_version,
            "evidence_id": rule.evidence_id,
            "evidence_text": rule.evidence_text,
            "severity": rule.severity,
            "action": rule.action,
            "status": rule.status,
        }
    if isinstance(rule, SafetyRuleSchema):
        return rule.model_dump()
    if isinstance(rule, Mapping):
        return dict(rule)
    raise RulePackValidationError(
        f"Malformed rule: expected mapping, got {type(rule).__name__}."
    )


class RulePackService:
    """Create, validate, persist, and verify versioned SafetyRule packs."""

    def __init__(self, session: Session, validator: Optional[RulePackValidator] = None):
        self.session = session
        self.validator = validator or RulePackValidator()

    def create_pack(
        self,
        pack_data: Mapping[str, Any],
        *,
        status: Optional[str] = None,
    ) -> RuleVersion:
        pack_name = _as_optional_text(pack_data.get("pack_name"))
        version = _as_optional_text(pack_data.get("version"))
        description = _as_optional_text(pack_data.get("description"))
        if pack_name is None or version is None:
            raise RulePackValidationError("Rule pack requires pack_name and version.")

        rules_raw = pack_data.get("rules")
        if not isinstance(rules_raw, list):
            raise RulePackValidationError("Rule pack 'rules' must be a list.")

        validated_maps = self._validate_rules(rules_raw)
        existing = (
            self.session.query(RuleVersion)
            .filter_by(pack_name=pack_name, version=version)
            .one_or_none()
        )
        if existing is not None:
            raise RulePackVersionExistsError(
                f"Rule pack '{pack_name}' version '{version}' already exists."
            )

        content_hash = compute_content_hash(validated_maps)
        version_status = _as_optional_text(status) or _as_optional_text(
            pack_data.get("status")
        ) or "active"

        rule_version = RuleVersion(
            pack_name=pack_name,
            version=version,
            description=description,
            rules_count=len(validated_maps),
            content_hash=content_hash,
            status=version_status,
        )
        self.session.add(rule_version)
        self.session.flush()

        for rule_map in validated_maps:
            record = SafetyRule(
                rule_id=rule_map["rule_id"],
                rule_type=rule_map["rule_type"],
                category=rule_map["category"],
                drug_a=rule_map["drug_a"],
                drug_b=rule_map.get("drug_b"),
                source=rule_map["source"],
                source_version=rule_map["source_version"],
                evidence_id=rule_map["evidence_id"],
                evidence_text=rule_map["evidence_text"],
                severity=rule_map["severity"],
                action=rule_map["action"],
                status=rule_map.get("status") or "active",
                version_id=rule_version.id,
            )
            self.session.add(record)

        self.session.commit()
        self.session.refresh(rule_version)
        return rule_version

    def load_pack(
        self,
        path: Union[str, Path],
        *,
        status: Optional[str] = None,
    ) -> RuleVersion:
        return self.create_pack(load_rule_pack_file(path), status=status)

    def get_active_rules(
        self,
        pack_name: Optional[str] = None,
        version: Optional[str] = None,
    ) -> List[SafetyRule]:
        query = (
            self.session.query(SafetyRule)
            .join(RuleVersion, SafetyRule.version_id == RuleVersion.id)
            .filter(SafetyRule.status == "active")
            .filter(RuleVersion.status == "active")
        )
        if pack_name is not None:
            query = query.filter(RuleVersion.pack_name == pack_name)
        if version is not None:
            query = query.filter(RuleVersion.version == version)
        return query.order_by(SafetyRule.rule_id.asc()).all()

    def verify_content_hash(
        self,
        rule_version: Union[RuleVersion, int],
    ) -> HashVerificationResult:
        if isinstance(rule_version, int):
            stored = self.session.get(RuleVersion, rule_version)
            if stored is None:
                raise RulePackError(f"RuleVersion id {rule_version} not found.")
        else:
            stored = rule_version
            if stored.id is not None:
                stored = self.session.get(RuleVersion, stored.id) or stored

        rules = (
            self.session.query(SafetyRule)
            .filter(SafetyRule.version_id == stored.id)
            .all()
        )
        computed = compute_content_hash([rule_to_mapping(rule) for rule in rules])
        return HashVerificationResult(
            matches=computed == stored.content_hash,
            stored_hash=stored.content_hash,
            computed_hash=computed,
        )

    def _validate_rules(self, rules: Iterable[Any]) -> List[Dict[str, Any]]:
        issues: List[RuleValidationIssue] = []
        mappings: List[Dict[str, Any]] = []

        for index, item in enumerate(rules):
            try:
                mapping = rule_to_mapping(item)
            except RulePackValidationError as exc:
                issues.append(
                    RuleValidationIssue(
                        rule_id=None,
                        field="rules",
                        message=f"Malformed rule at index {index}: {exc}",
                    )
                )
                continue

            rule_id = mapping.get("rule_id")
            for field in REQUIRED_PROVENANCE_FIELDS:
                if _is_blank(mapping.get(field)):
                    issues.append(
                        RuleValidationIssue(
                            rule_id=str(rule_id) if rule_id else None,
                            field=field,
                            message=f"Missing required field '{field}'.",
                        )
                    )

            if _is_blank(mapping.get("drug_a")):
                issues.append(
                    RuleValidationIssue(
                        rule_id=str(rule_id) if rule_id else None,
                        field="drug_a",
                        message="Malformed rule: drug_a is required by SafetyRule.",
                    )
                )

            rule_type = _as_optional_text(mapping.get("rule_type"))
            if rule_type is not None and rule_type not in ALLOWED_RULE_TYPES:
                issues.append(
                    RuleValidationIssue(
                        rule_id=str(rule_id) if rule_id else None,
                        field="rule_type",
                        message=(
                            f"Unsupported rule_type '{rule_type}'. "
                            f"Allowed: {sorted(ALLOWED_RULE_TYPES)}"
                        ),
                    )
                )

            category = _as_optional_text(mapping.get("category"))
            if category is not None and category not in ALLOWED_CATEGORIES:
                issues.append(
                    RuleValidationIssue(
                        rule_id=str(rule_id) if rule_id else None,
                        field="category",
                        message=(
                            f"Unsupported category '{category}'. "
                            f"Allowed: {sorted(ALLOWED_CATEGORIES)}"
                        ),
                    )
                )

            mappings.append(mapping)

        pack_result = self.validator.validate_pack(
            [m for m in mappings if not _is_blank(m.get("rule_id"))]
        )
        if not pack_result.is_valid:
            issues.extend(pack_result.errors)

        if issues:
            raise RulePackValidationError(
                "Rule pack failed validation.",
                issues=issues,
            )

        normalized: List[Dict[str, Any]] = []
        for mapping in mappings:
            canonical = canonicalize_rule(mapping)
            canonical["drug_a"] = str(canonical["drug_a"]).strip()
            normalized.append(canonical)
        return normalized
