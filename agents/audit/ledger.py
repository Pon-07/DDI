import contextlib
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from agents.audit.schemas import AuditRecord, ChainVerificationResult
from agents.audit.verifier import (
    GENESIS_PREV_HASH,
    calculate_event_hash,
    verify_audit_chain,
)


class AuditLedger:
    """
    Tamper-evident, append-only cryptographic audit ledger.
    Supports PostgreSQL for primary/production deployment and SQLite for test/development.
    Computes SHA-256 hashes linking each record to its predecessor.
    """

    def __init__(self, db_path: Union[str, Path, Engine] = "aegis_rx.db"):
        if hasattr(db_path, "dialect"):
            self.engine = db_path
            self.is_postgres = (db_path.dialect.name == "postgresql")
            self.db_path = str(db_path.url)
            self._mem_conn = None
        else:
            str_path = str(db_path)
            self.is_postgres = (
                str_path.startswith("postgresql")
                or str_path.startswith("postgres://")
            )

            if self.is_postgres:
                from database.database import normalize_database_url
                self.db_path = normalize_database_url(str_path)
                self.engine = create_engine(self.db_path, pool_pre_ping=True)
                self._mem_conn = None
            elif str_path.startswith("sqlite:///"):
                clean_path = str_path.replace("sqlite:///", "")
                self.db_path = clean_path if clean_path else ":memory:"
                self.engine = None
                self._mem_conn = sqlite3.connect(":memory:") if self.db_path == ":memory:" else None
                if self._mem_conn:
                    self._mem_conn.row_factory = sqlite3.Row
            else:
                self.db_path = str_path
                self.engine = None
                self._mem_conn = sqlite3.connect(":memory:") if self.db_path == ":memory:" else None
                if self._mem_conn:
                    self._mem_conn.row_factory = sqlite3.Row

        self._init_db()

    @contextlib.contextmanager
    def _connection(self):
        if self._mem_conn is not None:
            yield self._mem_conn
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

    def _init_db(self) -> None:
        """Initialize the audit_ledger table if it does not exist."""
        if self.is_postgres:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS audit_ledger (
                            id SERIAL PRIMARY KEY,
                            timestamp VARCHAR(100) NOT NULL,
                            actor VARCHAR(100) NOT NULL,
                            event_type VARCHAR(100) NOT NULL,
                            payload JSONB NOT NULL,
                            prev_hash VARCHAR(64) NOT NULL,
                            hash VARCHAR(64) UNIQUE NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS ix_audit_ledger_timestamp ON audit_ledger (timestamp);
                        CREATE INDEX IF NOT EXISTS ix_audit_ledger_actor ON audit_ledger (actor);
                        CREATE INDEX IF NOT EXISTS ix_audit_ledger_event_type ON audit_ledger (event_type);
                        CREATE INDEX IF NOT EXISTS ix_audit_ledger_prev_hash ON audit_ledger (prev_hash);
                        CREATE INDEX IF NOT EXISTS ix_audit_ledger_hash ON audit_ledger (hash);
                        """
                    )
                )
        else:
            with self._connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_ledger (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        prev_hash TEXT NOT NULL,
                        hash TEXT NOT NULL
                    )
                    """
                )
                conn.commit()

    def _sanitize_payload(self, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Sanitize payload before storing in audit ledger, avoiding unnecessary PHI retention.
        """
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            return {"data": str(payload)}

        sanitized = {}
        for k, v in payload.items():
            k_lower = str(k).lower()
            if k_lower in {"ssn", "social_security_number", "raw_phi"}:
                continue
            sanitized[k] = v
        return sanitized

    def append_event(
        self,
        actor: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        timestamp: Optional[Union[datetime, str]] = None,
    ) -> AuditRecord:
        """
        Append an immutable event to the audit ledger.
        Calculates SHA-256 hash chaining to the preceding entry.
        """
        if not actor or not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string.")
        if not event_type or not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must be a non-empty string.")

        sanitized_payload = self._sanitize_payload(payload)

        # Format timestamp in UTC ISO format
        if timestamp is None:
            timestamp_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        elif isinstance(timestamp, datetime):
            timestamp_str = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        else:
            timestamp_str = str(timestamp)

        if self.is_postgres:
            with self.engine.begin() as conn:
                last_row = conn.execute(
                    text("SELECT hash FROM audit_ledger ORDER BY id DESC LIMIT 1")
                ).fetchone()
                prev_hash = last_row[0] if last_row else GENESIS_PREV_HASH

                current_hash = calculate_event_hash(
                    prev_hash=prev_hash,
                    timestamp=timestamp_str,
                    actor=actor.strip(),
                    event_type=event_type.strip(),
                    payload=sanitized_payload,
                )

                payload_json = json.dumps(sanitized_payload, sort_keys=True)
                res = conn.execute(
                    text(
                        """
                        INSERT INTO audit_ledger (timestamp, actor, event_type, payload, prev_hash, hash)
                        VALUES (:ts, :act, :et, :pl, :ph, :h)
                        RETURNING id
                        """
                    ),
                    {
                        "ts": timestamp_str,
                        "act": actor.strip(),
                        "et": event_type.strip(),
                        "pl": payload_json,
                        "ph": prev_hash,
                        "h": current_hash,
                    },
                )
                record_id = res.scalar_one()

                return AuditRecord(
                    id=record_id,
                    timestamp=timestamp_str,
                    actor=actor.strip(),
                    event_type=event_type.strip(),
                    payload=sanitized_payload,
                    prev_hash=prev_hash,
                    hash=current_hash,
                )
        else:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT hash FROM audit_ledger ORDER BY id DESC LIMIT 1")
                last_row = cursor.fetchone()
                prev_hash = last_row["hash"] if last_row else GENESIS_PREV_HASH

                current_hash = calculate_event_hash(
                    prev_hash=prev_hash,
                    timestamp=timestamp_str,
                    actor=actor.strip(),
                    event_type=event_type.strip(),
                    payload=sanitized_payload,
                )

                payload_json = json.dumps(sanitized_payload, sort_keys=True)
                cursor.execute(
                    """
                    INSERT INTO audit_ledger (timestamp, actor, event_type, payload, prev_hash, hash)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp_str,
                        actor.strip(),
                        event_type.strip(),
                        payload_json,
                        prev_hash,
                        current_hash,
                    ),
                )
                record_id = cursor.lastrowid
                conn.commit()

                return AuditRecord(
                    id=record_id,
                    timestamp=timestamp_str,
                    actor=actor.strip(),
                    event_type=event_type.strip(),
                    payload=sanitized_payload,
                    prev_hash=prev_hash,
                    hash=current_hash,
                )

    def get_events(self, limit: Optional[int] = None) -> List[AuditRecord]:
        """Retrieve all audit ledger records in chronological order."""
        if self.is_postgres:
            with self.engine.connect() as conn:
                query = "SELECT id, timestamp, actor, event_type, payload, prev_hash, hash FROM audit_ledger ORDER BY id ASC"
                if limit and limit > 0:
                    query += f" LIMIT {int(limit)}"
                rows = conn.execute(text(query)).fetchall()

                records: List[AuditRecord] = []
                for row in rows:
                    pl = row[4]
                    if isinstance(pl, str):
                        payload_data = json.loads(pl)
                    elif isinstance(pl, dict):
                        payload_data = pl
                    else:
                        payload_data = json.loads(str(pl))

                    records.append(
                        AuditRecord(
                            id=row[0],
                            timestamp=row[1],
                            actor=row[2],
                            event_type=row[3],
                            payload=payload_data,
                            prev_hash=row[5],
                            hash=row[6],
                        )
                    )
                return records
        else:
            with self._connection() as conn:
                query = "SELECT id, timestamp, actor, event_type, payload, prev_hash, hash FROM audit_ledger ORDER BY id ASC"
                if limit and limit > 0:
                    query += f" LIMIT {int(limit)}"

                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()

                records: List[AuditRecord] = []
                for row in rows:
                    payload_data = json.loads(row["payload"])
                    records.append(
                        AuditRecord(
                            id=row["id"],
                            timestamp=row["timestamp"],
                            actor=row["actor"],
                            event_type=row["event_type"],
                            payload=payload_data,
                            prev_hash=row["prev_hash"],
                            hash=row["hash"],
                        )
                    )
                return records

    def get_event_by_id(self, record_id: int) -> Optional[AuditRecord]:
        """Retrieve a single audit ledger record by its ID."""
        if self.is_postgres:
            with self.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT id, timestamp, actor, event_type, payload, prev_hash, hash FROM audit_ledger WHERE id = :id"
                    ),
                    {"id": record_id},
                ).fetchone()
                if not row:
                    return None

                pl = row[4]
                if isinstance(pl, str):
                    payload_data = json.loads(pl)
                elif isinstance(pl, dict):
                    payload_data = pl
                else:
                    payload_data = json.loads(str(pl))

                return AuditRecord(
                    id=row[0],
                    timestamp=row[1],
                    actor=row[2],
                    event_type=row[3],
                    payload=payload_data,
                    prev_hash=row[5],
                    hash=row[6],
                )
        else:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, timestamp, actor, event_type, payload, prev_hash, hash FROM audit_ledger WHERE id = ?",
                    (record_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None

                return AuditRecord(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    actor=row["actor"],
                    event_type=row["event_type"],
                    payload=json.loads(row["payload"]),
                    prev_hash=row["prev_hash"],
                    hash=row["hash"],
                )

    def verify_chain(self) -> ChainVerificationResult:
        """Verify cryptographic integrity of the entire audit chain."""
        records = self.get_events()
        return verify_audit_chain(records)

    # Append-only enforcement guards
    def update_event(self, *args, **kwargs) -> None:
        raise RuntimeError("AuditLedger is append-only. Modification of existing records is prohibited.")

    def delete_event(self, *args, **kwargs) -> None:
        raise RuntimeError("AuditLedger is append-only. Deletion of existing records is prohibited.")
