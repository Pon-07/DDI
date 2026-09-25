import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from agents.knowledge.normalizer import DrugNormalizer
from models import Drug, LabelEvidence

logger = logging.getLogger(__name__)

MEANINGFUL_LABEL_SECTIONS: List[str] = [
    "dosage_and_administration",
    "contraindications",
    "drug_interactions",
    "warnings_and_cautions",
    "use_in_specific_populations",
]


class IngestionResult(BaseModel):
    """
    Summary report of an OpenFDA evidence ingestion execution.
    """

    model_config = ConfigDict(extra="forbid")

    files_processed: int = 0
    records_read: int = 0
    records_ingested: int = 0
    records_failed: int = 0
    drugs_created: int = 0
    drugs_reused: int = 0
    label_evidences_created: int = 0
    label_evidences_skipped: int = 0
    validation_errors: List[Dict[str, Any]] = Field(default_factory=list)


class OpenFDAIngestionAdapter:
    """
    Validated OpenFDA Evidence Ingestion Adapter for AEGIS Rx.
    Ingests normalized OpenFDA partitioned JSON datasets strictly into
    Drug and LabelEvidence SQLite tables.

    Guarantees:
      - Strictly ingests as LABEL EVIDENCE (never creates InteractionEvidence).
      - Never populates SafetyRule records from raw label text.
      - Idempotent execution (prevents duplicate Drug or LabelEvidence entries).
      - Preserves provenance (source, source_version, evidence_id, verbatim text).
      - Validates record structure and records explicit validation errors for malformed records.
    """

    def __init__(
        self,
        db_session: Session,
        normalizer: Optional[DrugNormalizer] = None,
        source_name: str = "openfda",
        source_version: Optional[str] = None,
        commit_interval: int = 500,
    ):
        self.session = db_session
        self.normalizer = normalizer or DrugNormalizer()
        self.source_name = source_name
        self.source_version = source_version
        self.commit_interval = commit_interval
        self._known_drugs: Dict[Tuple[str, str], int] = {}
        self._known_label_evidence: Set[Tuple[Optional[int], str, Optional[str], str]] = set()
        self.preload_cache()

    def preload_cache(self) -> None:
        """Preload existing Drug and LabelEvidence identifiers from database into in-memory lookup cache."""
        try:
            existing_drugs = (
                self.session.query(Drug.id, Drug.normalized_name, Drug.source).all()
            )
            for drug_id, norm_name, src in existing_drugs:
                if norm_name and src:
                    self._known_drugs[(norm_name, src)] = drug_id

            existing_labels = (
                self.session.query(
                    LabelEvidence.drug_id,
                    LabelEvidence.section,
                    LabelEvidence.evidence_id,
                    LabelEvidence.source,
                ).all()
            )
            for d_id, section, ev_id, src in existing_labels:
                self._known_label_evidence.add((d_id, section, ev_id, src))
        except Exception:
            # If tables are not created or empty, ignore
            pass

    def _is_valid_text(self, val: object) -> bool:
        """Check if a section text is non-empty and meaningful."""
        if val is None:
            return False
        text_str = str(val).strip()
        if not text_str or text_str.lower() in {"nan", "null", "none", "false", "true"}:
            return False
        return True

    def validate_record(
        self, raw_record: Any, index: int = 0, filename: str = ""
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Validate schema of a raw OpenFDA normalized record.
        Returns:
            Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
                (is_valid, error_reason, sanitized_extracted_data)
        """
        if not isinstance(raw_record, dict):
            return False, "Record is not a valid JSON dictionary object", None

        evidence = raw_record.get("evidence")
        if not evidence or not isinstance(evidence, dict):
            return False, "Missing or non-dictionary 'evidence' field", None

        generic_name = evidence.get("generic_name")
        brand_name = evidence.get("brand_name")
        drug_a = raw_record.get("drug_a")

        # Resolve primary drug name
        primary_name: Optional[str] = None
        if generic_name and self._is_valid_text(generic_name):
            primary_name = str(generic_name).strip()
        elif drug_a and self._is_valid_text(drug_a):
            primary_name = str(drug_a).strip()
        elif brand_name and self._is_valid_text(brand_name):
            primary_name = str(brand_name).strip()

        if not primary_name:
            return False, "Missing generic_name, drug_a, and brand_name in evidence", None

        evidence_id = evidence.get("id")
        clean_evidence_id = str(evidence_id).strip() if self._is_valid_text(evidence_id) else None

        record_source = raw_record.get("source") or self.source_name

        return True, None, {
            "primary_name": primary_name,
            "generic_name": str(generic_name).strip() if generic_name else None,
            "brand_name": str(brand_name).strip() if brand_name else None,
            "evidence_id": clean_evidence_id,
            "evidence": evidence,
            "source": str(record_source).strip() if record_source else self.source_name,
        }

    def ingest_records(
        self, records: List[Any], filename: str = "in_memory"
    ) -> IngestionResult:
        """
        Ingest a list of OpenFDA normalized record objects into SQLite tables.
        """
        result = IngestionResult()

        for idx, raw_record in enumerate(records):
            result.records_read += 1
            is_valid, error_reason, extracted = self.validate_record(
                raw_record, index=idx, filename=filename
            )

            if not is_valid or not extracted:
                result.records_failed += 1
                result.validation_errors.append(
                    {
                        "file": filename,
                        "record_index": idx,
                        "reason": error_reason or "Unknown validation error",
                        "raw_sample": str(raw_record)[:200],
                    }
                )
                continue

            primary_name = extracted["primary_name"]
            normalized_name = self.normalizer.normalize(primary_name)
            if not normalized_name:
                normalized_name = primary_name.lower().strip()

            evidence_id = extracted["evidence_id"]
            source_name = extracted["source"]
            evidence = extracted["evidence"]

            # 1. Get or create Drug record (Idempotent with cache)
            drug_key = (normalized_name, source_name)
            drug = None

            if drug_key in self._known_drugs:
                drug_id = self._known_drugs[drug_key]
                drug = self.session.get(Drug, drug_id)
                result.drugs_reused += 1
            else:
                drug = (
                    self.session.query(Drug)
                    .filter(
                        Drug.normalized_name == normalized_name,
                        Drug.source == source_name,
                    )
                    .first()
                )
                if drug:
                    self._known_drugs[drug_key] = drug.id
                    result.drugs_reused += 1
                else:
                    drug = Drug(
                        drug_name=primary_name,
                        normalized_name=normalized_name,
                        rxnorm_code=None,
                        source=source_name,
                        source_version=self.source_version,
                        label_id=evidence_id,
                    )
                    self.session.add(drug)
                    self.session.flush()  # Flush to obtain drug.id
                    self._known_drugs[drug_key] = drug.id
                    result.drugs_created += 1

            # 2. Extract and create LabelEvidence records for meaningful sections (Idempotent with cache)
            for section in MEANINGFUL_LABEL_SECTIONS:
                section_text = evidence.get(section)
                if not self._is_valid_text(section_text):
                    continue

                clean_text = str(section_text).strip()
                evidence_key = (drug.id, section, evidence_id, source_name)

                if evidence_key in self._known_label_evidence:
                    result.label_evidences_skipped += 1
                    continue

                # Check if LabelEvidence already exists in database
                existing_le = (
                    self.session.query(LabelEvidence)
                    .filter(
                        LabelEvidence.drug_id == drug.id,
                        LabelEvidence.section == section,
                        LabelEvidence.evidence_id == evidence_id,
                        LabelEvidence.source == source_name,
                    )
                    .first()
                )

                if existing_le:
                    self._known_label_evidence.add(evidence_key)
                    result.label_evidences_skipped += 1
                    continue

                label_record = LabelEvidence(
                    drug_id=drug.id,
                    drug_name=normalized_name,
                    section=section,
                    text=clean_text,
                    source=source_name,
                    source_version=self.source_version,
                    evidence_id=evidence_id,
                )
                self.session.add(label_record)
                self._known_label_evidence.add(evidence_key)
                result.label_evidences_created += 1

            result.records_ingested += 1

            # Periodic batch commit
            if result.records_read % self.commit_interval == 0:
                self.session.commit()

        # Final commit for the batch
        self.session.commit()
        return result

    def ingest_file(self, file_path: Union[str, Path]) -> IngestionResult:
        """
        Read and ingest an OpenFDA JSON partition file.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"OpenFDA dataset file not found: {path}")

        result = IngestionResult()
        result.files_processed += 1

        with open(path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                result.records_failed += 1
                result.validation_errors.append(
                    {
                        "file": path.name,
                        "record_index": 0,
                        "reason": f"Invalid JSON format: {str(e)}",
                    }
                )
                return result

        if isinstance(data, list):
            file_res = self.ingest_records(data, filename=path.name)
        elif isinstance(data, dict):
            file_res = self.ingest_records([data], filename=path.name)
        else:
            result.records_failed += 1
            result.validation_errors.append(
                {
                    "file": path.name,
                    "record_index": 0,
                    "reason": f"Top-level JSON is neither a list nor a dictionary: {type(data)}",
                }
            )
            return result

        file_res.files_processed = 1
        return file_res

    def ingest_directory(
        self, dir_path: Union[str, Path], file_pattern: str = "*.json"
    ) -> IngestionResult:
        """
        Discover and ingest all OpenFDA JSON partition files from a directory.
        """
        path = Path(dir_path)
        if not path.exists():
            raise FileNotFoundError(f"OpenFDA dataset directory not found: {path}")

        if path.is_file():
            return self.ingest_file(path)

        # Collect and sort JSON partition files (e.g. part1, part2, part3, part4)
        json_files = sorted(list(path.glob(file_pattern)))
        combined_result = IngestionResult()

        if not json_files:
            logger.warning("No JSON partition files found in %s matching '%s'", path, file_pattern)
            return combined_result

        for json_file in json_files:
            file_res = self.ingest_file(json_file)
            combined_result.files_processed += file_res.files_processed
            combined_result.records_read += file_res.records_read
            combined_result.records_ingested += file_res.records_ingested
            combined_result.records_failed += file_res.records_failed
            combined_result.drugs_created += file_res.drugs_created
            combined_result.drugs_reused += file_res.drugs_reused
            combined_result.label_evidences_created += file_res.label_evidences_created
            combined_result.label_evidences_skipped += file_res.label_evidences_skipped
            combined_result.validation_errors.extend(file_res.validation_errors)

        return combined_result
