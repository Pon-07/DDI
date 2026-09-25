import sys
from pathlib import Path

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from sqlalchemy import text
from database.database import Base, engine
from models import (  # noqa: F401
    AuditLedger,
    Drug,
    Event,
    Finding,
    InteractionEvidence,
    LabelEvidence,
    Lab,
    Medication,
    Order,
    Patient,
    RuleVersion,
    SafetyRule,
)

EXPECTED_TABLES = [
    "audit_ledger",
    "drugs",
    "events",
    "findings",
    "interaction_evidence",
    "label_evidence",
    "labs",
    "medications",
    "orders",
    "patients",
    "rule_versions",
    "safety_rules",
]


def init_db() -> None:
    """Initialize the SQLite database schema by creating all defined tables idempotently."""
    print("Initializing AEGIS Rx SQLite database...")

    # Ensure all tables are created idempotently
    Base.metadata.create_all(bind=engine)

    # Ensure schema migrations for existing tables (e.g. severity on interaction_evidence)
    with engine.begin() as conn:
        cols = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(interaction_evidence)")).fetchall()
        ]
        if cols and "severity" not in cols:
            conn.execute(text("ALTER TABLE interaction_evidence ADD COLUMN severity VARCHAR(50)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_interaction_evidence_severity ON interaction_evidence(severity)"))

    # Query SQLite database metadata for verification
    with engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar()

        result = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        )
        existing_tables = [row[0] for row in result.fetchall()]

    missing_tables = [t for t in EXPECTED_TABLES if t not in existing_tables]
    if missing_tables:
        raise RuntimeError(f"Database initialization failed. Missing tables: {missing_tables}")

    db_path = engine.url.database or "aegis_rx.db"
    fk_status = "ENABLED" if foreign_keys == 1 else "DISABLED"

    print("\n--- AEGIS Rx Database Verification ---")
    print(f"Database Path: {db_path}")
    print(f"Journal Mode:  {str(journal_mode).upper() if journal_mode else 'UNKNOWN'}")
    print(f"Foreign Keys:  {fk_status}")
    print(f"Table Count:   {len(existing_tables)} / {len(EXPECTED_TABLES)}")
    print("Verified Tables:")
    for table_name in existing_tables:
        print(f"  - {table_name}")
    print("--------------------------------------\n")


if __name__ == "__main__":
    init_db()
