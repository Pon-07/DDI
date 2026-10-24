import sqlite3
import tempfile
import unittest
from pathlib import Path

from agents.audit import (
    GENESIS_PREV_HASH,
    AuditLedger,
    AuditRecord,
    ChainVerificationResult,
    calculate_event_hash,
    verify_audit_chain,
)


class TestAuditLedger(unittest.TestCase):
    """Unit tests for the tamper-evident Audit Ledger and Verifier."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_audit.db"
        self.ledger = AuditLedger(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_first_event_creation(self):
        """Test appending the first (genesis) event to the ledger."""
        record = self.ledger.append_event(
            actor="system_startup",
            event_type="SYSTEM_INITIALIZED",
            payload={"version": "1.0.0", "environment": "test"},
        )

        self.assertIsInstance(record, AuditRecord)
        self.assertEqual(record.id, 1)
        self.assertEqual(record.actor, "system_startup")
        self.assertEqual(record.event_type, "SYSTEM_INITIALIZED")
        self.assertEqual(record.prev_hash, GENESIS_PREV_HASH)
        self.assertEqual(len(record.hash), 64)

        # Verify hash calculation matches
        expected_hash = calculate_event_hash(
            prev_hash=GENESIS_PREV_HASH,
            timestamp=record.timestamp,
            actor="system_startup",
            event_type="SYSTEM_INITIALIZED",
            payload={"version": "1.0.0", "environment": "test"},
        )
        self.assertEqual(record.hash, expected_hash)

    def test_multiple_chained_events(self):
        """Test appending multiple sequential events and verifying SHA-256 chain linkages."""
        rec1 = self.ledger.append_event(
            actor="dr_smith",
            event_type="ORDER_CREATED",
            payload={"patient_id": 101, "medication": "Lisinopril"},
        )
        rec2 = self.ledger.append_event(
            actor="risk_detector",
            event_type="RISK_EVALUATED",
            payload={"patient_id": 101, "findings_count": 1},
        )
        rec3 = self.ledger.append_event(
            actor="dr_smith",
            event_type="RESOLUTION_COSIGNED",
            payload={"finding_id": "DDI-001", "decision": "accepted"},
        )

        self.assertEqual(rec1.prev_hash, GENESIS_PREV_HASH)
        self.assertEqual(rec2.prev_hash, rec1.hash)
        self.assertEqual(rec3.prev_hash, rec2.hash)

        # Retrieve and verify all records from SQLite
        all_records = self.ledger.get_events()
        self.assertEqual(len(all_records), 3)
        self.assertEqual(all_records[0].id, 1)
        self.assertEqual(all_records[1].id, 2)
        self.assertEqual(all_records[2].id, 3)

    def test_valid_chain_verification(self):
        """Test verify_chain returns is_valid=True for an untampered ledger."""
        for i in range(5):
            self.ledger.append_event(
                actor=f"user_{i}",
                event_type="AUDIT_EVENT",
                payload={"index": i, "status": "active"},
            )

        verification = self.ledger.verify_chain()
        self.assertIsInstance(verification, ChainVerificationResult)
        self.assertTrue(verification.is_valid)
        self.assertEqual(verification.total_records, 5)
        self.assertEqual(len(verification.errors), 0)
        self.assertIsNone(verification.tampered_record_id)

    def test_tampered_payload_detection(self):
        """Test that direct database alteration of payload is detected by verify_chain."""
        self.ledger.append_event(
            actor="dr_jones",
            event_type="MEDICATION_PRESCRIBED",
            payload={"medication": "Warfarin", "dose": "5mg"},
        )
        self.ledger.append_event(
            actor="nurse_kelly",
            event_type="MEDICATION_ADMINISTERED",
            payload={"medication": "Warfarin", "dose": "5mg"},
        )

        # Tamper directly in SQLite table modifying the payload of record 1
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE audit_ledger SET payload = ? WHERE id = ?",
                ('{"medication": "Warfarin", "dose": "100mg"}', 1),
            )
            conn.commit()
        finally:
            conn.close()

        verification = self.ledger.verify_chain()
        self.assertFalse(verification.is_valid)
        self.assertEqual(verification.tampered_record_id, 1)
        self.assertTrue(any("mismatch" in e for e in verification.errors))

    def test_tampered_hash_detection(self):
        """Test that modifying a hash directly in SQLite is detected by verify_chain."""
        self.ledger.append_event(
            actor="system",
            event_type="EVENT_1",
            payload={"step": 1},
        )
        self.ledger.append_event(
            actor="system",
            event_type="EVENT_2",
            payload={"step": 2},
        )

        # Tamper with record 2 hash directly in DB
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE audit_ledger SET hash = ? WHERE id = ?",
                ("a" * 64, 2),
            )
            conn.commit()
        finally:
            conn.close()

        verification = self.ledger.verify_chain()
        self.assertFalse(verification.is_valid)
        self.assertEqual(verification.tampered_record_id, 2)
        self.assertTrue(any("mismatch" in e for e in verification.errors))

    def test_broken_prev_hash_linkage_detection(self):
        """Test that modifying a prev_hash directly in SQLite is detected by verify_chain."""
        self.ledger.append_event(actor="user_1", event_type="E1", payload={})
        self.ledger.append_event(actor="user_2", event_type="E2", payload={})

        # Tamper with record 2 prev_hash in DB
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE audit_ledger SET prev_hash = ? WHERE id = ?",
                ("b" * 64, 2),
            )
            conn.commit()
        finally:
            conn.close()

        verification = self.ledger.verify_chain()
        self.assertFalse(verification.is_valid)
        self.assertEqual(verification.tampered_record_id, 2)
        self.assertTrue(any("prev_hash linkage" in e or "mismatch" in e for e in verification.errors))

    def test_missing_or_deleted_record_detection(self):
        """Test that deleting a record from the chain is detected."""
        self.ledger.append_event(actor="u1", event_type="E1", payload={})
        self.ledger.append_event(actor="u2", event_type="E2", payload={})
        self.ledger.append_event(actor="u3", event_type="E3", payload={})

        # Delete intermediate record 2
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM audit_ledger WHERE id = 2")
            conn.commit()
        finally:
            conn.close()

        verification = self.ledger.verify_chain()
        self.assertFalse(verification.is_valid)
        self.assertTrue(any("Missing or out-of-order" in e or "linkage" in e for e in verification.errors))

    def test_append_only_guardrails(self):
        """Test that update and delete operations are prohibited at application layer."""
        with self.assertRaises(RuntimeError):
            self.ledger.update_event(1, payload={})

        with self.assertRaises(RuntimeError):
            self.ledger.delete_event(1)

    def test_payload_sanitization_excludes_sensitive_phi(self):
        """Test that unnecessary sensitive PHI keys like SSN are stripped before storing."""
        record = self.ledger.append_event(
            actor="admin",
            event_type="PATIENT_REGISTERED",
            payload={"patient_id": 99, "ssn": "000-11-2222", "department": "Cardiology"},
        )
        self.assertNotIn("ssn", record.payload)
        self.assertEqual(record.payload["patient_id"], 99)
        self.assertEqual(record.payload["department"], "Cardiology")


if __name__ == "__main__":
    unittest.main()
