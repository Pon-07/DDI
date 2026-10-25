import sys
from pathlib import Path

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from typing import Any, Optional

from sqlalchemy import inspect, text
from database.database import Base, engine
from engine.auth.models import AuthBase
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
    SubstitutionRequest,
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
    "substitution_requests",
]



def init_db(target_engine: Optional[Any] = None, verbose: bool = True) -> None:
    """Initialize database schema by creating all defined tables idempotently across PostgreSQL or SQLite."""
    eng = target_engine or engine
    db_type = "PostgreSQL" if eng.dialect.name == "postgresql" else "SQLite"
    if verbose:
        print(f"Initializing AEGIS Rx {db_type} database...")

    # Ensure all application and auth tables are created idempotently
    Base.metadata.create_all(bind=eng)

    inspector = inspect(eng)
    existing_tables = set(inspector.get_table_names())

    # Ensure schema migrations for existing tables
    with eng.begin() as conn:
        is_pg = conn.dialect.name == "postgresql"

        if "interaction_evidence" in existing_tables:
            ie_cols = {col["name"] for col in inspector.get_columns("interaction_evidence")}
            if "severity" not in ie_cols:
                conn.execute(text("ALTER TABLE interaction_evidence ADD COLUMN severity VARCHAR(50)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_interaction_evidence_severity ON interaction_evidence(severity)"))

        if "users" in existing_tables:
            user_cols = {col["name"] for col in inspector.get_columns("users")}
            if "totp_secret" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)"))
            if "totp_enabled" not in user_cols:
                default_val = "FALSE" if is_pg else "0"
                conn.execute(text(f"ALTER TABLE users ADD COLUMN totp_enabled BOOLEAN DEFAULT {default_val}"))
            if "totp_last_verified_at" not in user_cols:
                col_type = "TIMESTAMP" if is_pg else "DATETIME"
                conn.execute(text(f"ALTER TABLE users ADD COLUMN totp_last_verified_at {col_type}"))
            if "totp_last_timestep" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN totp_last_timestep INTEGER"))

    # Refresh inspector after migrations
    inspector = inspect(eng)
    current_tables = sorted(inspector.get_table_names())

    missing_tables = [t for t in EXPECTED_TABLES if t not in current_tables]
    if missing_tables:
        raise RuntimeError(f"Database initialization failed. Missing tables: {missing_tables}")

    db_path = eng.url.database or "aegis_rx"

    if verbose:
        print("\n--- AEGIS Rx Database Verification ---")
        print(f"Database Target: {db_type}")
        print(f"Database Name:   {db_path}")
        print(f"Dialect:         {eng.dialect.name}")
        print(f"Table Count:     {len(current_tables)} / {len(EXPECTED_TABLES)}")
        print("Verified Tables:")
        for table_name in current_tables:
            print(f"  - {table_name}")
        print("--------------------------------------\n")


if __name__ == "__main__":
    init_db()
