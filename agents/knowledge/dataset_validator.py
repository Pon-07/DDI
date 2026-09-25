"""
Reusable pre-ingestion validation for normalized JSON knowledge datasets.

Validates DDInter and OpenFDA records before any SQLite writes. Never infers
missing drug identity from free-text evidence fields.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from agents.knowledge.normalizer import DrugNormalizer

ALLOWED_SEVERITIES: Set[str] = {"major", "moderate", "minor", "unknown"}

PLACEHOLDER_VALUES: Set[str] = {"", "nan", "null", "none", "n/a", "na", "false", "true"}

DATASET_KIND_DDINTER = "ddinter"
DATASET_KIND_OPENFDA = "openfda"

CATEGORY_MALFORMED_RECORD = "malformed_record"
CATEGORY_MISSING_REQUIRED_FIELD = "missing_required_field"
CATEGORY_MISSING_DRUG_A = "missing_drug_a"
CATEGORY_MISSING_DRUG_B = "missing_drug_b"
CATEGORY_MISSING_SEVERITY = "missing_severity"
CATEGORY_INVALID_SEVERITY = "invalid_severity"
CATEGORY_SELF_INTERACTION = "self_interaction"
CATEGORY_DUPLICATE_RECORD = "duplicate_record"
CATEGORY_MISSING_DRUG_IDENTITY = "missing_drug_identity"
CATEGORY_SOURCE_INCONSISTENCY = "source_inconsistency"
CATEGORY_SCHEMA_TYPE = "schema_type"
CATEGORY_UNREADABLE_FILE = "unreadable_file"
CATEGORY_EMPTY_DATASET = "empty_dataset"

DEFAULT_ERROR_SAMPLE_LIMIT = 25


class DatasetValidationIssue(BaseModel):
    """A single representative validation defect."""

    model_config = ConfigDict(extra="forbid")

    record_index: int
    category: str
    message: str
    file: Optional[str] = None
    field: Optional[str] = None


class DatasetValidationResult(BaseModel):
    """Structured pre-ingestion validation summary."""

    model_config = ConfigDict(extra="forbid")

    dataset: str
    source: str
    source_version: Optional[str] = None
    total_records: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    error_counts: Dict[str, int] = Field(default_factory=dict)
    errors: List[DatasetValidationIssue] = Field(default_factory=list)
    passed: bool = False
    files_processed: int = 0


class DatasetValidationError(Exception):
    """Raised when pre-ingestion validation fails and ingestion must be aborted."""

    def __init__(self, result: DatasetValidationResult):
        self.result = result
        super().__init__(
            f"Dataset validation failed for {result.dataset}: "
            f"{result.invalid_records} invalid of {result.total_records} records"
        )


def is_meaningful_text(value: object) -> bool:
    """Return True when a field contains a non-placeholder scalar value."""
    if value is None:
        return False
    if isinstance(value, (dict, list, tuple, set)):
        return False
    text = str(value).strip()
    if not text:
        return False
    return text.lower() not in PLACEHOLDER_VALUES


def _as_clean_text(value: object) -> Optional[str]:
    if not is_meaningful_text(value):
        return None
    return str(value).strip()


class DatasetValidator:
    """
    Pre-ingestion validator for normalized JSON DDInter and OpenFDA datasets.

    Performs read-only checks on dataset files and in-memory records.
    Callers must abort ingestion when the returned result has passed=False.
    """

    def __init__(
        self,
        dataset_kind: str,
        source_name: Optional[str] = None,
        source_version: Optional[str] = None,
        normalizer: Optional[DrugNormalizer] = None,
        error_sample_limit: int = DEFAULT_ERROR_SAMPLE_LIMIT,
    ):
        kind = (dataset_kind or "").strip().lower()
        if kind not in {DATASET_KIND_DDINTER, DATASET_KIND_OPENFDA}:
            raise ValueError(
                f"Unsupported dataset_kind '{dataset_kind}'. "
                f"Expected '{DATASET_KIND_DDINTER}' or '{DATASET_KIND_OPENFDA}'."
            )
        self.dataset_kind = kind
        self.source_name = (source_name or kind).strip()
        self.source_version = source_version
        self.normalizer = normalizer or DrugNormalizer()
        self.error_sample_limit = error_sample_limit

    def validate_records(
        self,
        records: Sequence[Any],
        filename: str = "in_memory",
        seen_keys: Optional[Set[Tuple[Any, ...]]] = None,
        seen_sources: Optional[Set[str]] = None,
        seen_versions: Optional[Set[str]] = None,
    ) -> DatasetValidationResult:
        """Validate an in-memory sequence of normalized records."""
        result = self._empty_result()
        pair_keys = seen_keys if seen_keys is not None else set()
        sources = seen_sources if seen_sources is not None else set()
        versions = seen_versions if seen_versions is not None else set()
        error_counts: Counter[str] = Counter()

        if not isinstance(records, (list, tuple)):
            self._add_issue(
                result,
                error_counts,
                record_index=0,
                category=CATEGORY_MALFORMED_RECORD,
                message="Top-level dataset must be a JSON list of records",
                filename=filename,
                field="root",
            )
            return self._finalize(result, error_counts)

        result.total_records = len(records)
        if result.total_records == 0:
            self._add_issue(
                result,
                error_counts,
                record_index=0,
                category=CATEGORY_EMPTY_DATASET,
                message="Dataset contains no records",
                filename=filename,
                field="root",
            )
            result.invalid_records = 0
            return self._finalize(result, error_counts)

        for idx, raw_record in enumerate(records):
            issues = self._validate_one(
                raw_record,
                index=idx,
                filename=filename,
                seen_keys=pair_keys,
                seen_sources=sources,
                seen_versions=versions,
            )
            if issues:
                result.invalid_records += 1
                for issue in issues:
                    error_counts[issue.category] += 1
                    if len(result.errors) < self.error_sample_limit:
                        result.errors.append(issue)
            else:
                result.valid_records += 1

        return self._finalize(result, error_counts)

    def validate_file(self, file_path: Union[str, Path]) -> DatasetValidationResult:
        """Load and validate a single JSON file. Does not write to any database."""
        path = Path(file_path)
        result = self._empty_result()
        error_counts: Counter[str] = Counter()
        result.files_processed = 1

        if not path.exists():
            self._add_issue(
                result,
                error_counts,
                record_index=0,
                category=CATEGORY_UNREADABLE_FILE,
                message=f"Dataset file does not exist: {path}",
                filename=str(path),
                field="file",
            )
            return self._finalize(result, error_counts)

        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._add_issue(
                result,
                error_counts,
                record_index=0,
                category=CATEGORY_UNREADABLE_FILE,
                message=f"Unable to read JSON dataset: {exc}",
                filename=path.name,
                field="file",
            )
            return self._finalize(result, error_counts)

        if isinstance(data, list):
            records: List[Any] = data
        elif isinstance(data, dict):
            records = [data]
        else:
            self._add_issue(
                result,
                error_counts,
                record_index=0,
                category=CATEGORY_MALFORMED_RECORD,
                message=(
                    "Top-level JSON is neither a list nor a dictionary: "
                    f"{type(data).__name__}"
                ),
                filename=path.name,
                field="root",
            )
            return self._finalize(result, error_counts)

        file_result = self.validate_records(records, filename=path.name)
        file_result.files_processed = 1
        return file_result

    def validate_path(
        self,
        input_path: Union[str, Path],
        file_pattern: str = "*.json",
    ) -> DatasetValidationResult:
        """Validate a JSON file or all matching JSON files in a directory."""
        path = Path(input_path)
        if not path.exists():
            result = self._empty_result()
            error_counts: Counter[str] = Counter()
            self._add_issue(
                result,
                error_counts,
                record_index=0,
                category=CATEGORY_UNREADABLE_FILE,
                message=f"Dataset path does not exist: {path}",
                filename=str(path),
                field="path",
            )
            return self._finalize(result, error_counts)

        if path.is_file():
            return self.validate_file(path)

        json_files = sorted(path.glob(file_pattern))
        if not json_files:
            result = self._empty_result()
            error_counts = Counter()
            self._add_issue(
                result,
                error_counts,
                record_index=0,
                category=CATEGORY_EMPTY_DATASET,
                message=f"No JSON files found matching '{file_pattern}' in {path}",
                filename=str(path),
                field="path",
            )
            return self._finalize(result, error_counts)

        combined = self._empty_result()
        error_counts = Counter()
        seen_keys: Set[Tuple[Any, ...]] = set()
        seen_sources: Set[str] = set()
        seen_versions: Set[str] = set()

        for json_file in json_files:
            try:
                with open(json_file, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                combined.files_processed += 1
                self._add_issue(
                    combined,
                    error_counts,
                    record_index=0,
                    category=CATEGORY_UNREADABLE_FILE,
                    message=f"Unable to read JSON dataset: {exc}",
                    filename=json_file.name,
                    field="file",
                )
                continue

            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                records = [data]
            else:
                combined.files_processed += 1
                self._add_issue(
                    combined,
                    error_counts,
                    record_index=0,
                    category=CATEGORY_MALFORMED_RECORD,
                    message=(
                        "Top-level JSON is neither a list nor a dictionary: "
                        f"{type(data).__name__}"
                    ),
                    filename=json_file.name,
                    field="root",
                )
                continue

            file_result = self.validate_records(
                records,
                filename=json_file.name,
                seen_keys=seen_keys,
                seen_sources=seen_sources,
                seen_versions=seen_versions,
            )
            combined.files_processed += 1
            combined.total_records += file_result.total_records
            combined.valid_records += file_result.valid_records
            combined.invalid_records += file_result.invalid_records
            for category, count in file_result.error_counts.items():
                error_counts[category] += count
            remaining = self.error_sample_limit - len(combined.errors)
            if remaining > 0:
                combined.errors.extend(file_result.errors[:remaining])

        if combined.total_records == 0 and CATEGORY_EMPTY_DATASET not in error_counts:
            self._add_issue(
                combined,
                error_counts,
                record_index=0,
                category=CATEGORY_EMPTY_DATASET,
                message="No records found in matched JSON files",
                filename=str(path),
                field="path",
            )

        return self._finalize(combined, error_counts)

    def _validate_one(
        self,
        raw_record: Any,
        index: int,
        filename: str,
        seen_keys: Set[Tuple[Any, ...]],
        seen_sources: Set[str],
        seen_versions: Set[str],
    ) -> List[DatasetValidationIssue]:
        issues: List[DatasetValidationIssue] = []

        if not isinstance(raw_record, dict):
            issues.append(
                self._issue(
                    index,
                    CATEGORY_MALFORMED_RECORD,
                    "Record is not a valid JSON dictionary object",
                    filename,
                    "root",
                )
            )
            return issues

        self._check_source_consistency(raw_record, index, filename, issues, seen_sources, seen_versions)

        if self.dataset_kind == DATASET_KIND_DDINTER:
            self._validate_ddinter(raw_record, index, filename, issues, seen_keys)
        else:
            self._validate_openfda(raw_record, index, filename, issues, seen_keys)

        return issues

    def _validate_ddinter(
        self,
        raw_record: Dict[str, Any],
        index: int,
        filename: str,
        issues: List[DatasetValidationIssue],
        seen_keys: Set[Tuple[Any, ...]],
    ) -> None:
        drug_a_raw = raw_record.get("drug_a")
        drug_b_raw = raw_record.get("drug_b")
        severity_raw = raw_record.get("severity")

        self._assert_optional_string_field(raw_record, "drug_a", index, filename, issues)
        self._assert_optional_string_field(raw_record, "drug_b", index, filename, issues)
        self._assert_optional_string_field(raw_record, "severity", index, filename, issues)
        self._assert_optional_string_field(
            raw_record, "interaction_description", index, filename, issues
        )

        if not is_meaningful_text(drug_a_raw):
            issues.append(
                self._issue(
                    index,
                    CATEGORY_MISSING_DRUG_A,
                    "Missing or empty 'drug_a' field",
                    filename,
                    "drug_a",
                )
            )
        if not is_meaningful_text(drug_b_raw):
            issues.append(
                self._issue(
                    index,
                    CATEGORY_MISSING_DRUG_B,
                    "Missing or empty 'drug_b' field",
                    filename,
                    "drug_b",
                )
            )
        if not is_meaningful_text(severity_raw):
            issues.append(
                self._issue(
                    index,
                    CATEGORY_MISSING_SEVERITY,
                    "Missing or empty 'severity' field",
                    filename,
                    "severity",
                )
            )
        elif str(severity_raw).strip().lower() not in ALLOWED_SEVERITIES:
            issues.append(
                self._issue(
                    index,
                    CATEGORY_INVALID_SEVERITY,
                    (
                        "Invalid severity value; expected Major, Moderate, "
                        f"Minor, or Unknown (got {severity_raw!r})"
                    ),
                    filename,
                    "severity",
                )
            )

        drug_a_text = _as_clean_text(drug_a_raw)
        drug_b_text = _as_clean_text(drug_b_raw)
        if drug_a_text and drug_b_text:
            norm_a = self._normalize_name(drug_a_text)
            norm_b = self._normalize_name(drug_b_text)
            if norm_a == norm_b:
                issues.append(
                    self._issue(
                        index,
                        CATEGORY_SELF_INTERACTION,
                        "Self-interaction: drug_a and drug_b identify the same drug",
                        filename,
                        "drug_a",
                    )
                )
            pair_key = ("ddinter_pair", norm_a, norm_b)
            if pair_key in seen_keys:
                issues.append(
                    self._issue(
                        index,
                        CATEGORY_DUPLICATE_RECORD,
                        f"Duplicate directed pair ({drug_a_text!r}, {drug_b_text!r})",
                        filename,
                        "drug_a",
                    )
                )
            else:
                seen_keys.add(pair_key)

    def _validate_openfda(
        self,
        raw_record: Dict[str, Any],
        index: int,
        filename: str,
        issues: List[DatasetValidationIssue],
        seen_keys: Set[Tuple[Any, ...]],
    ) -> None:
        evidence = raw_record.get("evidence")
        if evidence is None or "evidence" not in raw_record:
            issues.append(
                self._issue(
                    index,
                    CATEGORY_MISSING_REQUIRED_FIELD,
                    "Missing required 'evidence' field",
                    filename,
                    "evidence",
                )
            )
            return
        if not isinstance(evidence, dict):
            issues.append(
                self._issue(
                    index,
                    CATEGORY_MALFORMED_RECORD,
                    "Missing or non-dictionary 'evidence' field",
                    filename,
                    "evidence",
                )
            )
            return

        self._assert_optional_string_field(raw_record, "drug_a", index, filename, issues)
        self._assert_optional_string_field(evidence, "generic_name", index, filename, issues)
        self._assert_optional_string_field(evidence, "brand_name", index, filename, issues)

        drug_a = _as_clean_text(raw_record.get("drug_a"))
        generic_name = _as_clean_text(evidence.get("generic_name")) or _as_clean_text(
            raw_record.get("generic_name")
        )
        brand_name = _as_clean_text(evidence.get("brand_name")) or _as_clean_text(
            raw_record.get("brand_name")
        )

        # Never infer identity from free-text label sections.
        if not drug_a and not generic_name and not brand_name:
            issues.append(
                self._issue(
                    index,
                    CATEGORY_MISSING_DRUG_IDENTITY,
                    "Missing drug identity: drug_a, generic_name, and brand_name are all empty",
                    filename,
                    "drug_a",
                )
            )

        evidence_id = _as_clean_text(evidence.get("id"))
        if evidence_id:
            dup_key: Optional[Tuple[Any, ...]] = ("openfda_id", evidence_id.lower())
        elif drug_a or generic_name or brand_name:
            identity = (drug_a or "", generic_name or "", brand_name or "")
            dup_key = ("openfda_identity",) + tuple(part.lower() for part in identity)
        else:
            dup_key = None

        if dup_key is not None:
            if dup_key in seen_keys:
                issues.append(
                    self._issue(
                        index,
                        CATEGORY_DUPLICATE_RECORD,
                        "Duplicate OpenFDA record",
                        filename,
                        "evidence.id",
                    )
                )
            else:
                seen_keys.add(dup_key)

    def _check_source_consistency(
        self,
        raw_record: Dict[str, Any],
        index: int,
        filename: str,
        issues: List[DatasetValidationIssue],
        seen_sources: Set[str],
        seen_versions: Set[str],
    ) -> None:
        source_val = raw_record.get("source")
        if source_val is not None and source_val != "":
            if isinstance(source_val, (dict, list)):
                issues.append(
                    self._issue(
                        index,
                        CATEGORY_SCHEMA_TYPE,
                        "Field 'source' must be a string",
                        filename,
                        "source",
                    )
                )
            elif is_meaningful_text(source_val):
                source_text = str(source_val).strip().lower()
                expected = self.source_name.strip().lower()
                if source_text != expected:
                    issues.append(
                        self._issue(
                            index,
                            CATEGORY_SOURCE_INCONSISTENCY,
                            (
                                f"Record source {source_val!r} does not match "
                                f"expected source {self.source_name!r}"
                            ),
                            filename,
                            "source",
                        )
                    )
                seen_sources.add(source_text)
                if len(seen_sources) > 1:
                    issues.append(
                        self._issue(
                            index,
                            CATEGORY_SOURCE_INCONSISTENCY,
                            "Mixed source values within the dataset",
                            filename,
                            "source",
                        )
                    )

        version_val = raw_record.get("source_version")
        if version_val is not None and version_val != "" and is_meaningful_text(version_val):
            version_text = str(version_val).strip()
            if self.source_version and version_text != str(self.source_version).strip():
                issues.append(
                    self._issue(
                        index,
                        CATEGORY_SOURCE_INCONSISTENCY,
                        (
                            f"Record source_version {version_text!r} does not match "
                            f"expected source_version {self.source_version!r}"
                        ),
                        filename,
                        "source_version",
                    )
                )
            seen_versions.add(version_text.lower())
            if len(seen_versions) > 1:
                issues.append(
                    self._issue(
                        index,
                        CATEGORY_SOURCE_INCONSISTENCY,
                        "Mixed source_version values within the dataset",
                        filename,
                        "source_version",
                    )
                )

    def _assert_optional_string_field(
        self,
        container: Dict[str, Any],
        field_name: str,
        index: int,
        filename: str,
        issues: List[DatasetValidationIssue],
    ) -> None:
        if field_name not in container:
            return
        value = container.get(field_name)
        if value is None or value == "":
            return
        if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set, int, float)):
            issues.append(
                self._issue(
                    index,
                    CATEGORY_SCHEMA_TYPE,
                    f"Field '{field_name}' must be a string scalar, not {type(value).__name__}",
                    filename,
                    field_name,
                )
            )

    def _normalize_name(self, drug_name: str) -> str:
        normalized = self.normalizer.normalize(drug_name)
        if normalized:
            return normalized
        return drug_name.lower().strip()

    def _empty_result(self) -> DatasetValidationResult:
        return DatasetValidationResult(
            dataset=self.dataset_kind,
            source=self.source_name,
            source_version=self.source_version,
            passed=False,
        )

    def _issue(
        self,
        record_index: int,
        category: str,
        message: str,
        filename: str,
        field: Optional[str],
    ) -> DatasetValidationIssue:
        return DatasetValidationIssue(
            record_index=record_index,
            category=category,
            message=message,
            file=filename,
            field=field,
        )

    def _add_issue(
        self,
        result: DatasetValidationResult,
        error_counts: Counter[str],
        record_index: int,
        category: str,
        message: str,
        filename: str,
        field: Optional[str],
    ) -> None:
        error_counts[category] += 1
        if len(result.errors) < self.error_sample_limit:
            result.errors.append(
                self._issue(record_index, category, message, filename, field)
            )

    def _finalize(
        self,
        result: DatasetValidationResult,
        error_counts: Counter[str],
    ) -> DatasetValidationResult:
        result.error_counts = dict(error_counts)
        result.passed = (
            result.total_records > 0
            and result.invalid_records == 0
            and sum(result.error_counts.values()) == 0
        )
        return result


def print_validation_summary(result: DatasetValidationResult) -> None:
    """Print a formatted pre-ingestion validation report."""
    status = "PASS" if result.passed else "FAIL"
    print("\n==================================================")
    print("      AEGIS Rx Dataset Validation Summary         ")
    print("==================================================")
    print(f"  Dataset:                 {result.dataset}")
    print(f"  Source:                  {result.source}")
    print(f"  Source Version:          {result.source_version or '-'}")
    print(f"  Files Processed:         {result.files_processed}")
    print(f"  Total Records:           {result.total_records:,}")
    print(f"  Valid Records:           {result.valid_records:,}")
    print(f"  Invalid Records:         {result.invalid_records:,}")
    print(f"  Status:                  {status}")
    if result.error_counts:
        print("  Error Counts by Category:")
        for category, count in sorted(result.error_counts.items()):
            print(f"    - {category}: {count:,}")
    print("==================================================")
    if result.errors:
        print("\nRepresentative Validation Errors:")
        for err in result.errors:
            location = f"{err.file}:{err.record_index}" if err.file else str(err.record_index)
            field = f" ({err.field})" if err.field else ""
            print(f"  - [{location}] [{err.category}]{field} {err.message}")
        print()


def validate_dataset(
    input_path: Union[str, Path],
    dataset_kind: str,
    source_name: Optional[str] = None,
    source_version: Optional[str] = None,
    file_pattern: str = "*.json",
) -> DatasetValidationResult:
    """Convenience entry point for pre-ingestion dataset validation."""
    validator = DatasetValidator(
        dataset_kind=dataset_kind,
        source_name=source_name,
        source_version=source_version,
    )
    return validator.validate_path(input_path, file_pattern=file_pattern)
