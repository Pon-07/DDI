import unittest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from database.database import Base, engine, get_db
from models import (
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
from scripts.init_db import EXPECTED_TABLES, init_db


class TestDatabaseInitialization(unittest.TestCase):
    """Unit tests for the production-ready AEGIS Rx database initialization."""

    def test_init_db_creates_all_expected_tables(self):
        """Verify that init_db() creates all 12 expected tables."""
        init_db()

        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            )
            existing_tables = [row[0] for row in result.fetchall()]

        for expected in EXPECTED_TABLES:
            self.assertIn(expected, existing_tables)

        self.assertEqual(len(existing_tables), 12)

    def test_init_db_is_idempotent(self):
        """Verify that calling init_db() multiple times does not raise or mutate data."""
        init_db()
        init_db()  # Second call must succeed smoothly

        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
            count = result.scalar()
            self.assertEqual(count, 12)

    def test_wal_journal_mode_enabled(self):
        """Verify that SQLite WAL journal mode is active."""
        with engine.connect() as conn:
            journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            self.assertEqual(str(journal_mode).lower(), "wal")

    def test_foreign_key_enforcement_enabled(self):
        """Verify that foreign key enforcement is enabled and active."""
        with engine.connect() as conn:
            fk_status = conn.execute(text("PRAGMA foreign_keys")).scalar()
            self.assertEqual(fk_status, 1)

    def test_foreign_key_constraint_blocks_orphaned_records(self):
        """Verify that inserting a record with a non-existent foreign key raises IntegrityError."""
        db_gen = get_db()
        db = next(db_gen)
        try:
            # Attempt to insert medication for non-existent patient ID 999999
            orphan_med = Medication(
                patient_id=999999,
                drug_name="Test Drug",
                status="active",
            )
            db.add(orphan_med)
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
