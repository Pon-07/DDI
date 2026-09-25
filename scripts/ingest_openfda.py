import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from agents.knowledge.openfda_adapter import (
    IngestionResult,
    OpenFDAIngestionAdapter,
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


def print_ingestion_summary(result: IngestionResult, elapsed_time: float = 0.0) -> None:
    """Print formatted OpenFDA ingestion report."""
    print("\n==================================================")
    print("      AEGIS Rx OpenFDA Ingestion Summary          ")
    print("==================================================")
    print(f"  Partitions Processed:            {result.files_processed}")
    print(f"  Total Records Read:              {result.records_read:,}")
    print(f"  Records Ingested Successfully:   {result.records_ingested:,}")
    print(f"  Malformed/Rejected Records:      {result.records_failed:,}")
    print(f"  Drugs Created:                   {result.drugs_created:,}")
    print(f"  Drugs Reused:                    {result.drugs_reused:,}")
    print(f"  Label Evidence Created:          {result.label_evidences_created:,}")
    print(f"  Label Evidence Skipped (Dups):   {result.label_evidences_skipped:,}")
    print(f"  Validation Errors:               {len(result.validation_errors):,}")
    if elapsed_time > 0:
        print(f"  Execution Time:                  {elapsed_time:.2f}s")
    print("==================================================")
    if result.validation_errors:
        print("\nValidation Error Samples (first 5):")
        for err in result.validation_errors[:5]:
            print(f"  - [{err.get('file')}:{err.get('record_index')}] {err.get('reason')}")
        if len(result.validation_errors) > 5:
            print(f"  ... and {len(result.validation_errors) - 5} more validation errors.")
    print()


def run_ingestion(
    input_dir: str | Path,
    batch_size: int = 500,
    file_pattern: str = "openFDA_normalized_part*.json",
    source_name: str = "openfda",
    source_version: Optional[str] = "2026.1",
    db_session: Optional[Any] = None,
) -> IngestionResult:
    """
    Execute OpenFDA evidence ingestion from partitioned JSON files into SQLite database.
    Idempotent and safe: never deletes existing database data.

    Args:
        input_dir: Directory containing partition JSON files or single JSON file path.
        batch_size: Database commit interval.
        file_pattern: Glob pattern for matching partition files.
        source_name: Source provenance name (default: "openfda").
        source_version: Source provenance version (default: "2026.1").
        db_session: Optional SQLAlchemy Session.

    Returns:
        IngestionResult: Summary metrics of ingestion.
    """
    path = Path(input_dir)
    if not path.exists():
        raise FileNotFoundError(f"OpenFDA dataset input path does not exist: {path}")

    # Ensure SQLite tables exist idempotently
    Base.metadata.create_all(bind=engine)

    session = db_session or SessionLocal()
    close_session = db_session is None

    try:
        adapter = OpenFDAIngestionAdapter(
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
        description="AEGIS Rx — Validated OpenFDA Label Evidence Ingestion CLI"
    )
    parser.add_argument(
        "--input-dir",
        "-i",
        type=str,
        default="data_validation/openFDA/normalized_parts",
        help="Path to directory containing OpenFDA JSON partition files (default: data_validation/openFDA/normalized_parts)",
    )
    parser.add_argument(
        "--pattern",
        "-p",
        type=str,
        default="openFDA_normalized_part*.json",
        help="File pattern for partition discovery (default: openFDA_normalized_part*.json)",
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

    args = parser.parse_args(argv)

    if args.verify_only:
        counts = verify_database()
        print_verification_summary(counts)
        return 0

    print(f"Starting OpenFDA evidence ingestion from: {args.input_dir}")
    print(f"Discovery pattern: {args.pattern} | Batch size: {args.batch_size}")

    start_time = time.time()
    try:
        result = run_ingestion(
            input_dir=args.input_dir,
            batch_size=args.batch_size,
            file_pattern=args.pattern,
            source_version=args.source_version,
        )
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
