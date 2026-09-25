import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from agents.knowledge.dataset_validator import (
    DatasetValidationError,
    print_validation_summary,
    validate_dataset,
)
from agents.knowledge.ddinter_adapter import (
    DDInterIngestionAdapter,
    DDInterIngestionResult,
)
from database.database import Base, SessionLocal, engine
from models import Drug, InteractionEvidence, LabelEvidence, SafetyRule


def verify_database(db_session: Optional[Any] = None) -> Dict[str, int]:
    """
    Query the SQLite database to verify row counts across key intelligence tables.

    Returns:
        Dict[str, int]: Mapping of table names to row counts.
    """
    session = db_session or SessionLocal()
    close_session = db_session is None

    try:
        counts = {
            "Drug": session.query(Drug).count(),
            "LabelEvidence": session.query(LabelEvidence).count(),
            "InteractionEvidence": session.query(InteractionEvidence).count(),
            "SafetyRule": session.query(SafetyRule).count(),
        }
        return counts
    finally:
        if close_session:
            session.close()


def print_verification_summary(counts: Dict[str, int]) -> None:
    """Print formatted database verification report."""
    print("\n==================================================")
    print("      AEGIS Rx Database Verification Summary      ")
    print("==================================================")
    print(f"  Drug Rows:                {counts.get('Drug', 0):,}")
    print(f"  LabelEvidence Rows:       {counts.get('LabelEvidence', 0):,}")
    print(f"  InteractionEvidence Rows: {counts.get('InteractionEvidence', 0):,}")
    print(f"  SafetyRule Rows:          {counts.get('SafetyRule', 0):,}")
    print("==================================================\n")


def print_ingestion_summary(
    result: DDInterIngestionResult, elapsed_time: float = 0.0
) -> None:
    """Print formatted DDInter ingestion report."""
    print("\n==================================================")
    print("      AEGIS Rx DDInter Ingestion Summary          ")
    print("==================================================")
    print(f"  Files Processed:                 {result.files_processed}")
    print(f"  Total Records Read:              {result.records_read:,}")
    print(f"  Records Ingested Successfully:   {result.records_ingested:,}")
    print(f"  Malformed/Rejected Records:      {result.records_failed:,}")
    print(f"  Drugs Created:                   {result.drugs_created:,}")
    print(f"  Drugs Reused:                    {result.drugs_reused:,}")
    print(f"  Interaction Evidence Created:    {result.interaction_evidences_created:,}")
    print(f"  Interaction Evidence Skipped:    {result.interaction_evidences_skipped:,}")
    print(f"  Validation Errors:               {len(result.validation_errors):,}")
    if elapsed_time > 0:
        print(f"  Execution Time:                  {elapsed_time:.2f}s")
    print("==================================================")
    if result.validation_errors:
        print("\nValidation Error Samples (first 5):")
        for err in result.validation_errors[:5]:
            print(
                f"  - [{err.get('file')}:{err.get('record_index')}] {err.get('reason')}"
            )
        if len(result.validation_errors) > 5:
            print(
                f"  ... and {len(result.validation_errors) - 5} more validation errors."
            )
    print()


def run_ingestion(
    input_path: str | Path,
    batch_size: int = 500,
    file_pattern: str = "*.json",
    source_name: str = "ddinter",
    source_version: Optional[str] = "2026.1",
    db_session: Optional[Any] = None,
    enforce_prevalidation: bool = False,
) -> DDInterIngestionResult:
    """
    Execute DDInter interaction evidence ingestion from JSON file/directory into SQLite database.
    Idempotent and safe: never deletes existing database data.

    Args:
        input_path: Path to ddinter_normalized.json or directory containing JSON files.
        batch_size: Database commit interval.
        file_pattern: Glob pattern if path is a directory.
        source_name: Source provenance name (default: "ddinter").
        source_version: Source provenance version (default: "2026.1").
        db_session: Optional SQLAlchemy Session.
        enforce_prevalidation: When True, abort before any writes if validation fails.

    Returns:
        DDInterIngestionResult: Summary metrics of ingestion.
    """
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"DDInter dataset input path does not exist: {path}")

    if enforce_prevalidation:
        validation = validate_dataset(
            path,
            dataset_kind="ddinter",
            source_name=source_name,
            source_version=source_version,
            file_pattern=file_pattern,
        )
        if not validation.passed:
            raise DatasetValidationError(validation)

    session = db_session or SessionLocal()
    close_session = db_session is None

    try:
        # Ensure SQLite tables exist idempotently on the target session engine
        target_engine = session.get_bind() if hasattr(session, "get_bind") else engine
        Base.metadata.create_all(bind=target_engine)
        adapter = DDInterIngestionAdapter(
            db_session=session,
            source_name=source_name,
            source_version=source_version,
            commit_interval=batch_size,
        )

        if path.is_file():
            result = adapter.ingest_file(path)
        else:
            result = adapter.ingest_directory(path, file_pattern=file_pattern)

        return result
    finally:
        if close_session:
            session.close()


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="AEGIS Rx — Validated DDInter Interaction Evidence Ingestion CLI"
    )
    parser.add_argument(
        "--input-path",
        "-i",
        type=str,
        default="data_validation/ddinter_normalized.json",
        help="Path to DDInter JSON dataset file or directory (default: data_validation/ddinter_normalized.json)",
    )
    parser.add_argument(
        "--pattern",
        "-p",
        type=str,
        default="*.json",
        help="File pattern for directory discovery (default: *.json)",
    )
    parser.add_argument(
        "--batch-size",
        "-b",
        type=int,
        default=500,
        help="Database commit batch size (default: 500)",
    )
    parser.add_argument(
        "--source-version",
        "-v",
        type=str,
        default="2026.1",
        help="Dataset version tag for provenance (default: 2026.1)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify current database row counts without running ingestion",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip database verification after ingestion",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Run pre-ingestion dataset validation only (does not write to the database)",
    )
    parser.add_argument(
        "--skip-prevalidation",
        action="store_true",
        help="Skip the pre-ingestion dataset validation gate",
    )

    args = parser.parse_args(argv)

    if args.verify_only:
        counts = verify_database()
        print_verification_summary(counts)
        return 0

    if args.validate_only or not args.skip_prevalidation:
        validation = validate_dataset(
            args.input_path,
            dataset_kind="ddinter",
            source_name="ddinter",
            source_version=args.source_version,
            file_pattern=args.pattern,
        )
        print_validation_summary(validation)
        if args.validate_only:
            return 0 if validation.passed else 1
        if not validation.passed:
            print(
                "\n[ERROR] Ingestion aborted: dataset validation failed.",
                file=sys.stderr,
            )
            return 1

    print(f"Starting DDInter interaction evidence ingestion from: {args.input_path}")
    print(f"Batch size: {args.batch_size} | Source version: {args.source_version}")

    start_time = time.time()
    try:
        result = run_ingestion(
            input_path=args.input_path,
            batch_size=args.batch_size,
            file_pattern=args.pattern,
            source_version=args.source_version,
            enforce_prevalidation=False,
        )
    except DatasetValidationError as e:
        print_validation_summary(e.result)
        print(f"\n[ERROR] Ingestion aborted: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[ERROR] Ingestion failed: {e}", file=sys.stderr)
        return 1

    elapsed = time.time() - start_time
    print_ingestion_summary(result, elapsed_time=elapsed)

    if not args.no_verify:
        counts = verify_database()
        print_verification_summary(counts)

    return 0


if __name__ == "__main__":
    sys.exit(main())
