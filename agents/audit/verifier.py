import hashlib
import json
from typing import Any, List, Optional

from agents.audit.schemas import AuditRecord, ChainVerificationResult

GENESIS_PREV_HASH = "0" * 64


def calculate_event_hash(
    prev_hash: str,
    timestamp: str,
    actor: str,
    event_type: str,
    payload: Any,
) -> str:
    """
    Calculate the SHA-256 hash using:
    previous hash + canonical JSON representation of the current event.
    """
    canonical_event = {
        "actor": str(actor),
        "event_type": str(event_type),
        "payload": payload if payload is not None else {},
        "timestamp": str(timestamp),
    }
    canonical_json = json.dumps(
        canonical_event,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    message = f"{prev_hash}{canonical_json}"
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def verify_audit_chain(records: List[AuditRecord]) -> ChainVerificationResult:
    """
    Cryptographically verify the integrity of an audit ledger chain.
    Detects:
      - Modified/tampered payloads
      - Modified hashes
      - Broken previous-hash linkages
      - Missing or deleted intermediate records
    """
    if not records:
        return ChainVerificationResult(
            is_valid=True,
            total_records=0,
            errors=[],
            tampered_record_id=None,
        )

    errors: List[str] = []
    tampered_id: Optional[int] = None
    expected_id = records[0].id

    for i, record in enumerate(records):
        # 1. Verify sequential IDs for missing/deleted record detection
        if record.id != expected_id:
            errors.append(
                f"Missing or out-of-order record detected: record ID is {record.id}, but expected {expected_id}."
            )
            if tampered_id is None:
                tampered_id = record.id

        expected_id = record.id + 1

        # 2. Check previous-hash linkage
        if i == 0:
            if record.prev_hash != GENESIS_PREV_HASH:
                errors.append(
                    f"Invalid genesis prev_hash '{record.prev_hash}' at record ID {record.id}. Expected '{GENESIS_PREV_HASH}'."
                )
                if tampered_id is None:
                    tampered_id = record.id
        else:
            prev_record = records[i - 1]
            if record.prev_hash != prev_record.hash:
                errors.append(
                    f"Broken prev_hash linkage at record ID {record.id}: prev_hash '{record.prev_hash}' "
                    f"does not match preceding record {prev_record.id} hash '{prev_record.hash}'."
                )
                if tampered_id is None:
                    tampered_id = record.id

        # 3. Recalculate and verify hash (detects payload tampering or hash modifications)
        recalculated_hash = calculate_event_hash(
            prev_hash=record.prev_hash,
            timestamp=record.timestamp,
            actor=record.actor,
            event_type=record.event_type,
            payload=record.payload,
        )

        if record.hash != recalculated_hash:
            errors.append(
                f"Cryptographic hash mismatch at record ID {record.id}: stored hash '{record.hash}' "
                f"does not match calculated hash '{recalculated_hash}'."
            )
            if tampered_id is None:
                tampered_id = record.id

    return ChainVerificationResult(
        is_valid=len(errors) == 0,
        total_records=len(records),
        errors=errors,
        tampered_record_id=tampered_id,
    )
