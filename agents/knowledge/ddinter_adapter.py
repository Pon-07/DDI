import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from agents.knowledge.normalizer import DrugNormalizer
from models import Drug, InteractionEvidence

logger = logging.getLogger(__name__)


class DDInterIngestionResult(BaseModel):
    """
    Summary report of a DDInter interaction evidence ingestion execution.
    """

    model_config = ConfigDict(extra="forbid")

    files_processed: int = 0
    records_read: int = 0
    records_ingested: int = 0
    records_failed: int = 0
    drugs_created: int = 0
    drugs_reused: int = 0
    interaction_evidences_created: int = 0
    interaction_evidences_skipped: int = 0
    validation_errors: List[Dict[str, Any]] = Field(default_factory=list)


# Alias for compatibility with general IngestionResult references
IngestionResult = DDInterIngestionResult


class DDInterIngestionAdapter:
    """
    Validated DDInter Interaction Evidence Ingestion Adapter for AEGIS Rx.
    Ingests normalized DDInter JSON datasets strictly into Drug and
    InteractionEvidence SQLite tables.

    Guarantees:
      - Strictly ingests validated drug pairs into InteractionEvidence.
      - Never populates SafetyRule records from raw DDI pairs.
      - Preserves exact source severity (Major, Moderate, Minor, Unknown) without inventing or transforming values.
      - Preserves original drug names in Drug.drug_name and InteractionEvidence.drug_a/b.
      - Never automatically creates synthetic reverse interaction pairs.
      - Idempotent execution (prevents duplicate Drug or InteractionEvidence entries).
      - Preserves provenance (source, source_version, evidence_id/DDInter ID string).
      - Validates record schema and records explicit validation errors for malformed records.
    """

    def __init__(
        self,
        db_session: Session,
        normalizer: Optional[DrugNormalizer] = None,
        source_name: str = "ddinter",
        source_version: Optional[str] = None,
        commit_interval: int = 500,
    ):
        self.session = db_session
        self.normalizer = normalizer or DrugNormalizer()
        self.source_name = source_name
        self.source_version = source_version
        self.commit_interval = commit_interval
        self._known_drugs: Dict[Tuple[str, str], int] = {}
        self._known_interactions: Set[Tuple[str, str, str, Optional[str]]] = set()
        self.preload_cache()

    def preload_cache(self) -> None:
        """Preload existing Drug and InteractionEvidence identifiers from database into in-memory lookup cache."""
        try:
            existing_drugs = (
                self.session.query(Drug.id, Drug.normalized_name, Drug.source).all()
            )
            for drug_id, norm_name, src in existing_drugs:
                if norm_name and src:
                    self._known_drugs[(norm_name, src)] = drug_id

            existing_interactions = (
                self.session.query(
                    InteractionEvidence.drug_a,
                    InteractionEvidence.drug_b,
                    InteractionEvidence.source,
                    InteractionEvidence.evidence_id,
                ).all()
            )
            for d_a, d_b, src, ev_id in existing_interactions:
                n_a = self.normalizer.normalize(d_a) or d_a.lower().strip()
                n_b = self.normalizer.normalize(d_b) or d_b.lower().strip()
                self._known_interactions.add((n_a, n_b, src, ev_id))
        except Exception:
            # If tables are uninitialized or empty, ignore
            pass

    def _is_valid_text(self, val: object) -> bool:
        """Check if a field value is non-empty and meaningful (allows 'unknown')."""
        if val is None:
            return False
        text_str = str(val).strip()
        if not text_str or text_str.lower() in {"nan", "null", "none"}:
            return False
        return True

    def validate_record(
        self, raw_record: Any, index: int = 0, filename: str = ""
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Validate schema of a raw DDInter normalized record.

        Returns:
            Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
                (is_valid, error_reason, sanitized_extracted_data)
        """
        if not isinstance(raw_record, dict):
            return False, "Record is not a valid JSON dictionary object", None

        drug_a = raw_record.get("drug_a")
        if not self._is_valid_text(drug_a):
            return False, "Missing or empty 'drug_a' field", None

        drug_b = raw_record.get("drug_b")
        if not self._is_valid_text(drug_b):
            return False, "Missing or empty 'drug_b' field", None

        severity = raw_record.get("severity")
        if not self._is_valid_text(severity):
            return False, "Missing or empty 'severity' field", None

        clean_drug_a = str(drug_a).strip()
        clean_drug_b = str(drug_b).strip()
        clean_severity = str(severity).strip()

        desc = raw_record.get("interaction_description")
        clean_desc = str(desc).strip() if self._is_valid_text(desc) else clean_severity

        evidence = raw_record.get("evidence")
        clean_evidence_id: Optional[str] = None
        if self._is_valid_text(evidence):
            clean_evidence_id = str(evidence).strip()

        source = raw_record.get("source")
        clean_source = str(source).strip() if self._is_valid_text(source) else self.source_name

        return True, None, {
            "drug_a": clean_drug_a,
            "drug_b": clean_drug_b,
            "severity": clean_severity,
            "description": clean_desc,
            "evidence_id": clean_evidence_id,
            "source": clean_source,
        }

    def _get_or_create_drug(self, drug_name: str, source_name: str, result: DDInterIngestionResult) -> Drug:
        """Get existing Drug record or create a new one, updating cache and metrics."""
        norm_name = self.normalizer.normalize(drug_name)
        if not norm_name:
            norm_name = drug_name.lower().strip()

        drug_key = (norm_name, source_name)
        if drug_key in self._known_drugs:
            drug_id = self._known_drugs[drug_key]
            drug = self.session.get(Drug, drug_id)
            result.drugs_reused += 1
            return drug

        existing_drug = (
            self.session.query(Drug)
            .filter(
                Drug.normalized_name == norm_name,
                Drug.source == source_name,
            )
            .first()
        )
        if existing_drug:
            self._known_drugs[drug_key] = existing_drug.id
            result.drugs_reused += 1
            return existing_drug

        new_drug = Drug(
            drug_name=drug_name,
            normalized_name=norm_name,
            rxnorm_code=None,
            source=source_name,
            source_version=self.source_version,
            label_id=None,
        )
        self.session.add(new_drug)
        self.session.flush()
        self._known_drugs[drug_key] = new_drug.id
        result.drugs_created += 1
        return new_drug

    def ingest_records(
        self, records: List[Any], filename: str = "in_memory"
    ) -> DDInterIngestionResult:
        """
        Ingest a list of DDInter normalized record objects into SQLite tables.
        """
        result = DDInterIngestionResult()

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

            drug_a_raw = extracted["drug_a"]
            drug_b_raw = extracted["drug_b"]
            severity_val = extracted["severity"]
            desc_val = extracted["description"]
            evidence_id = extracted["evidence_id"]
            source_name = extracted["source"]

            norm_a = self.normalizer.normalize(drug_a_raw) or drug_a_raw.lower().strip()
            norm_b = self.normalizer.normalize(drug_b_raw) or drug_b_raw.lower().strip()

            # 1. Get or create Drug records for both medications
            self._get_or_create_drug(drug_a_raw, source_name, result)
            self._get_or_create_drug(drug_b_raw, source_name, result)

            # 2. Idempotently insert InteractionEvidence
            interaction_key = (norm_a, norm_b, source_name, evidence_id)

            if interaction_key in self._known_interactions:
                result.interaction_evidences_skipped += 1
            else:
                existing_ie = (
                    self.session.query(InteractionEvidence)
                    .filter(
                        InteractionEvidence.drug_a == drug_a_raw,
                        InteractionEvidence.drug_b == drug_b_raw,
                        InteractionEvidence.source == source_name,
                        InteractionEvidence.evidence_id == evidence_id,
                    )
                    .first()
                )

                if existing_ie:
                    self._known_interactions.add(interaction_key)
                    result.interaction_evidences_skipped += 1
                else:
                    ie = InteractionEvidence(
                        drug_a=drug_a_raw,
                        drug_b=drug_b_raw,
                        description=desc_val,
                        severity=severity_val,
                        source=source_name,
                        source_version=self.source_version,
                        evidence_id=evidence_id,
                    )
                    self.session.add(ie)
                    self._known_interactions.add(interaction_key)
                    result.interaction_evidences_created += 1

            result.records_ingested += 1

            # Periodic batch commit
            if result.records_read % self.commit_interval == 0:
                self.session.commit()

        # Final commit for the batch
        self.session.commit()
        return result

    def ingest_file(self, file_path: Union[str, Path]) -> DDInterIngestionResult:
        """
        Read and ingest a DDInter JSON file.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"DDInter dataset file not found: {path}")

        result = DDInterIngestionResult()
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
    ) -> DDInterIngestionResult:
        """
        Discover and ingest all DDInter JSON files from a directory.
        """
        path = Path(dir_path)
        if not path.exists():
            raise FileNotFoundError(f"DDInter dataset directory not found: {path}")

        if path.is_file():
            return self.ingest_file(path)

        json_files = sorted(list(path.glob(file_pattern)))
        combined_result = DDInterIngestionResult()

        if not json_files:
            logger.warning("No JSON files found in %s matching '%s'", path, file_pattern)
            return combined_result

        for json_file in json_files:
            file_res = self.ingest_file(json_file)
            combined_result.files_processed += file_res.files_processed
            combined_result.records_read += file_res.records_read
            combined_result.records_ingested += file_res.records_ingested
            combined_result.records_failed += file_res.records_failed
            combined_result.drugs_created += file_res.drugs_created
            combined_result.drugs_reused += file_res.drugs_reused
            combined_result.interaction_evidences_created += file_res.interaction_evidences_created
            combined_result.interaction_evidences_skipped += file_res.interaction_evidences_skipped
            combined_result.validation_errors.extend(file_res.validation_errors)

        return combined_result
