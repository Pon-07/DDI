import contextlib
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Union

from agents.audit.schemas import AuditRecord, ChainVerificationResult
from agents.audit.verifier import (
    GENESIS_PREV_HASH,
    calculate_event_hash,
    verify_audit_chain,
)


class AuditLedger:
    """
    Tamper-evident, append-only cryptographic audit ledger stored in SQLite.
    Computes SHA-256 hashes linking each record to its predecessor.
    """

    def __init__(self, db_path: Union[str, Path] = "aegis_rx.db"):
        if isinstance(db_path, str) and db_path.startswith("sqlite:///"):
            # Strip SQLAlchemy sqlite prefix if provided
            clean_path = db_path.replace("sqlite:///", "")
            self.db_path = clean_path if clean_path else ":memory:"
        else:
            self.db_path = str(db_path)

        self._init_db()

    @contextlib.contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize the audit_ledger SQLite table if it does not exist."""
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
            # Exclude raw SSN or direct sensitive identifiers if inadvertently present
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

        with self._connection() as conn:
            cursor = conn.cursor()
            # Retrieve latest hash
            cursor.execute("SELECT hash FROM audit_ledger ORDER BY id DESC LIMIT 1")
            last_row = cursor.fetchone()
            prev_hash = last_row["hash"] if last_row else GENESIS_PREV_HASH

            # Compute SHA-256 hash
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
        """
        Retrieve all audit ledger records in chronological order.
        """
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
        """
        Retrieve a single audit ledger record by its ID.
        """
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
        """
        Verify cryptographic integrity of the entire audit chain in SQLite.
        """
        records = self.get_events()
        return verify_audit_chain(records)

    # Append-only enforcement guards
    def update_event(self, *args, **kwargs) -> None:
        raise RuntimeError("AuditLedger is append-only. Modification of existing records is prohibited.")

    def delete_event(self, *args, **kwargs) -> None:
        raise RuntimeError("AuditLedger is append-only. Deletion of existing records is prohibited.")
