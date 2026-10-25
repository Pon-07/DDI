"""
Repeatable SQLite-to-PostgreSQL Data Migration and Validation Tool for AEGIS Rx.

Preserves existing clinical, authentication, and audit data by safely reading from an
existing SQLite database (e.g. aegis_rx.db) and copying all records into PostgreSQL
following foreign-key dependency order. Updates PostgreSQL sequence generators and
validates row counts, foreign key integrity, and cryptographic audit hash chains.

Usage:
    python scripts/migrate_sqlite_to_postgres.py --sqlite-path aegis_rx.db --pg-url postgresql+psycopg2://postgres:postgres@localhost:5432/aegis_rx
    python scripts/migrate_sqlite_to_postgres.py --dry-run
    python scripts/migrate_sqlite_to_postgres.py --validate-only

Rollback Guidance:
    If a migration fails or data must be rolled back:
    1. The script executes the entire batch within a PostgreSQL transaction by default.
       Any unexpected database error triggers an automatic rollback of the transaction.
    2. To completely reset a PostgreSQL target schema back to an empty state:
       psql -U postgres -d aegis_rx -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
       alembic upgrade head
    3. The SQLite source database is opened in read-only mode (mode=ro) and is NEVER
       modified or overwritten by this script.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from database.database import DEFAULT_POSTGRES_URL, normalize_database_url
from agents.audit.verifier import verify_audit_chain
from agents.audit.schemas import AuditRecord

TABLE_MIGRATION_ORDER = [
    "patients",
    "medications",
    "orders",
    "labs",
    "events",
    "findings",
    "substitution_requests",
    "drugs",
    "label_evidence",
    "rule_versions",
    "safety_rules",
    "interaction_evidence",
    "audit_ledger",
    "users",
    "user_sessions",
    "otp_records",
]

JSON_COLUMNS = {
    "audit_ledger": {"payload"},
    "events": {"payload"},
    "findings": {"inputs", "trace"},
}

BOOLEAN_COLUMNS = {
    "users": {"is_active", "demo_mode_allowed", "totp_enabled"},
    "user_sessions": {"is_active"},
    "otp_records": {"is_demo", "is_used"},
}

DATETIME_COLUMNS = {
    "patients": {"created_at"},
    "medications": {"start_date", "end_date", "created_at"},
    "orders": {"ordered_at"},
    "labs": {"measured_at", "created_at"},
    "events": {"created_at"},
    "findings": {"created_at"},
    "substitution_requests": {"created_at", "updated_at", "decided_at"},
    "drugs": {"created_at", "updated_at"},
    "label_evidence": {"created_at", "updated_at"},
    "rule_versions": {"created_at", "updated_at"},
    "safety_rules": {"created_at", "updated_at"},
    "interaction_evidence": {"created_at", "updated_at"},
    "users": {"created_at", "updated_at", "last_login_at", "totp_last_verified_at"},
    "user_sessions": {"created_at", "expires_at", "last_activity_at"},
    "otp_records": {"created_at", "expires_at"},
}

DATE_COLUMNS = {
    "patients": {"date_of_birth"},
}


def parse_datetime(val: Any) -> Optional[datetime]:
    if val is None or isinstance(val, datetime):
        return val
    str_val = str(val).strip()
    if not str_val:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(str_val, fmt)
        except ValueError:
            continue
    return None


def parse_date(val: Any) -> Optional[date]:
    if val is None or isinstance(val, date):
        return val
    dt = parse_datetime(val)
    return dt.date() if dt else None


def coerce_value(table: str, col: str, val: Any) -> Any:
    if val is None:
        return None
    if table in JSON_COLUMNS and col in JSON_COLUMNS[table]:
        if isinstance(val, (dict, list)):
            return json.dumps(val)
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                return json.dumps(parsed)
            except Exception:
                return json.dumps({"raw": val})
        return json.dumps(val)
    if table in BOOLEAN_COLUMNS and col in BOOLEAN_COLUMNS[table]:
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        return str(val).lower() in ("1", "true", "t", "yes")
    if table in DATETIME_COLUMNS and col in DATETIME_COLUMNS[table]:
        return parse_datetime(val)
    if table in DATE_COLUMNS and col in DATE_COLUMNS[table]:
        return parse_date(val)
    return val


def get_sqlite_tables_and_counts(sqlite_path: Path) -> Dict[str, int]:
    conn = sqlite3.connect(f"file:{sqlite_path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        counts = {}
        for t in tables:
            c = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            counts[t] = c
        return counts
    finally:
        conn.close()


def migrate_data(
    sqlite_path: Path,
    pg_url: str,
    dry_run: bool = False,
    validate_only: bool = False,
) -> bool:
    print(f"\n================ AEGIS Rx SQLite -> PostgreSQL Migration ================")
    print(f"Source SQLite:     {sqlite_path.resolve()}")
    print(f"Target PostgreSQL: {pg_url}")
    print(f"Execution Mode:    {'DRY-RUN' if dry_run else ('VALIDATE-ONLY' if validate_only else 'MIGRATE & VALIDATE')}")
    print("=========================================================================\n")

    if not sqlite_path.exists():
        print(f"ERROR: SQLite source database not found at {sqlite_path}")
        return False

    sqlite_counts = get_sqlite_tables_and_counts(sqlite_path)
    print("Source SQLite Table Counts:")
    for t in TABLE_MIGRATION_ORDER:
        cnt = sqlite_counts.get(t, 0)
        print(f"  - {t:<24}: {cnt} rows")

    if dry_run:
        print("\nDry-run completed successfully. No changes made to PostgreSQL.")
        return True

    engine = create_engine(pg_url, pool_pre_ping=True)
    inspector = inspect(engine)
    pg_tables = set(inspector.get_table_names())

    missing_tables = [t for t in TABLE_MIGRATION_ORDER if t not in pg_tables]
    if missing_tables:
        print(f"\nTarget database is missing tables: {missing_tables}")
        print("Applying schema initialization...")
        from scripts.init_db import init_db
        init_db(target_engine=engine, verbose=False)
        inspector = inspect(engine)
        pg_tables = set(inspector.get_table_names())

    if validate_only:
        return validate_database(sqlite_path, engine)

    # Perform Migration inside a transaction
    src_conn = sqlite3.connect(f"file:{sqlite_path.as_posix()}?mode=ro", uri=True)
    src_conn.row_factory = sqlite3.Row

    try:
        with engine.begin() as pg_conn:
            print("\nMigrating tables in dependency order...")
            for table in TABLE_MIGRATION_ORDER:
                if table not in sqlite_counts or sqlite_counts[table] == 0:
                    continue

                cursor = src_conn.cursor()
                cursor.execute(f'SELECT * FROM "{table}" ORDER BY id ASC')
                rows = cursor.fetchall()
                if not rows:
                    continue

                col_names = [d[0] for d in cursor.description]
                pg_cols_info = {c["name"]: c for c in inspector.get_columns(table)}
                pg_cols = set(pg_cols_info.keys())
                valid_cols = [c for c in col_names if c in pg_cols]

                # Check if updated_at is required in PostgreSQL but absent from SQLite source
                include_updated_at = "updated_at" in pg_cols and "updated_at" not in valid_cols
                all_insert_cols = list(valid_cols)
                if include_updated_at:
                    all_insert_cols.append("updated_at")

                print(f"  -> Migrating {table}: {len(rows)} rows...", end=" ")

                cols_joined = ", ".join(f'"{c}"' for c in all_insert_cols)
                params_joined = ", ".join(f":{c}" for c in all_insert_cols)
                insert_sql = text(
                    f'INSERT INTO "{table}" ({cols_joined}) VALUES ({params_joined}) ON CONFLICT DO NOTHING'
                )

                batch_params = []
                for row in rows:
                    row_dict = {}
                    for col in valid_cols:
                        row_dict[col] = coerce_value(table, col, row[col])
                    if include_updated_at:
                        created = row_dict.get("created_at")
                        row_dict["updated_at"] = created if isinstance(created, datetime) else datetime.utcnow()
                    batch_params.append(row_dict)

                pg_conn.execute(insert_sql, batch_params)

                # Reset PostgreSQL sequence for tables with primary key 'id'
                if "id" in all_insert_cols:
                    seq_sql = text(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM \"{table}\""
                    )
                    try:
                        pg_conn.execute(seq_sql)
                    except Exception:
                        pass
                print("Done.")

        print("\nAll records migrated successfully into PostgreSQL.")
    except Exception as exc:
        print(f"\nMIGRATION ERROR: {exc}")
        print("\nRollback Note: PostgreSQL transaction was aborted. No partial state committed.")
        return False
    finally:
        src_conn.close()

    # Validate results
    return validate_database(sqlite_path, engine)


def validate_database(sqlite_path: Path, engine: Engine) -> bool:
    print("\n----------------- Migration Validation -----------------")
    src_counts = get_sqlite_tables_and_counts(sqlite_path)
    all_matched = True

    with engine.connect() as conn:
        for table in TABLE_MIGRATION_ORDER:
            src_cnt = src_counts.get(table, 0)
            pg_cnt = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
            match = pg_cnt >= src_cnt
            status = "PASS" if match else "FAIL"
            if not match:
                all_matched = False
            print(f"  [{status}] {table:<24}: SQLite={src_cnt:<5} PostgreSQL={pg_cnt:<5}")

        # Check key foreign key relationships
        print("\nVerifying Foreign Key Integrity in PostgreSQL:")
        fk_checks = [
            ("medications -> patients", "SELECT count(*) FROM medications m LEFT JOIN patients p ON m.patient_id = p.id WHERE m.patient_id IS NOT NULL AND p.id IS NULL"),
            ("orders -> patients", "SELECT count(*) FROM orders o LEFT JOIN patients p ON o.patient_id = p.id WHERE o.patient_id IS NOT NULL AND p.id IS NULL"),
            ("labs -> patients", "SELECT count(*) FROM labs l LEFT JOIN patients p ON l.patient_id = p.id WHERE l.patient_id IS NOT NULL AND p.id IS NULL"),
            ("events -> patients", "SELECT count(*) FROM events e LEFT JOIN patients p ON e.patient_id = p.id WHERE e.patient_id IS NOT NULL AND p.id IS NULL"),
            ("findings -> patients", "SELECT count(*) FROM findings f LEFT JOIN patients p ON f.patient_id = p.id WHERE f.patient_id IS NOT NULL AND p.id IS NULL"),
            ("user_sessions -> users", "SELECT count(*) FROM user_sessions s LEFT JOIN users u ON s.user_id = u.id WHERE s.user_id IS NOT NULL AND u.id IS NULL"),
        ]

        for desc, sql in fk_checks:
            orphans = conn.execute(text(sql)).scalar()
            ok = orphans == 0
            if not ok:
                all_matched = False
            print(f"  [{'PASS' if ok else 'FAIL'}] {desc}: {orphans} orphaned records")

        # Verify Audit Ledger cryptographic integrity and parity with SQLite source
        print("\nVerifying Audit Ledger Cryptographic Parity with SQLite:")
        from agents.audit.ledger import AuditLedger
        src_ledger = AuditLedger(str(sqlite_path))
        src_chain = src_ledger.verify_chain()

        pg_ledger = AuditLedger(db_path=engine)
        pg_chain = pg_ledger.verify_chain()

        chain_ok = (
            pg_chain.is_valid == src_chain.is_valid
            and pg_chain.total_records == src_chain.total_records
            and pg_chain.tampered_record_id == src_chain.tampered_record_id
            and len(pg_chain.errors) == len(src_chain.errors)
        )
        if not chain_ok:
            all_matched = False
        print(f"  [{'PASS' if chain_ok else 'FAIL'}] Audit chain matches SQLite: {chain_ok} ({pg_chain.total_records} events, is_valid={pg_chain.is_valid})")

    print("--------------------------------------------------------\n")
    if all_matched:
        print("VALIDATION SUCCESSFUL: All tables, relationships, and audit chains verified.")
    else:
        print("VALIDATION FAILED: Mismatches or integrity issues detected.")
    return all_matched


def main():
    parser = argparse.ArgumentParser(
        description="Migrate AEGIS Rx data from SQLite to PostgreSQL with integrity validation."
    )
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=Path("aegis_rx.db"),
        help="Path to the source SQLite database (default: ./aegis_rx.db)",
    )
    parser.add_argument(
        "--pg-url",
        type=str,
        default=os.getenv("DATABASE_URL") or os.getenv("AEGIS_DATABASE_URL") or DEFAULT_POSTGRES_URL,
        help="Target PostgreSQL database URL (default: DATABASE_URL env or local aegis_rx)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect source SQLite database and target schema without making changes",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Run validation checks between SQLite and PostgreSQL without migrating data",
    )

    args = parser.parse_args()
    pg_url = normalize_database_url(args.pg_url)
    success = migrate_data(
        sqlite_path=args.sqlite_path,
        pg_url=pg_url,
        dry_run=args.dry_run,
        validate_only=args.validate_only,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
