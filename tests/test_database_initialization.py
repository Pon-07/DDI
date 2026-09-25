import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from database.database import set_sqlite_pragma
from models import Medication
from scripts.init_db import EXPECTED_TABLES, init_db


class TestDatabaseInitialization(unittest.TestCase):
    """Unit tests for the production-ready AEGIS Rx database initialization on isolated test databases."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_init.db"

        # Create isolated test engine with standard SQLite pragmas (WAL + foreign keys)
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False},
        )
        event.listen(self.engine, "connect", set_sqlite_pragma)

        self.Session = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
        )

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_init_db_creates_all_expected_tables(self):
        """Verify that init_db() creates all 12 expected tables."""
        init_db(target_engine=self.engine, verbose=False)

        with self.engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            )
            existing_tables = [row[0] for row in result.fetchall()]

        for expected in EXPECTED_TABLES:
            self.assertIn(expected, existing_tables)

        self.assertEqual(len(existing_tables), 12)

    def test_init_db_is_idempotent(self):
        """Verify that calling init_db() multiple times does not raise or mutate data."""
        init_db(target_engine=self.engine, verbose=False)
        init_db(target_engine=self.engine, verbose=False)  # Second call must succeed smoothly

        with self.engine.connect() as conn:
            result = conn.execute(
                text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            )
            count = result.scalar()
            self.assertEqual(count, 12)

    def test_wal_journal_mode_enabled(self):
        """Verify that SQLite WAL journal mode is active on SQLite file engines."""
        init_db(target_engine=self.engine, verbose=False)
        with self.engine.connect() as conn:
            journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            self.assertEqual(str(journal_mode).lower(), "wal")

    def test_foreign_key_enforcement_enabled(self):
        """Verify that foreign key enforcement is enabled and active."""
        init_db(target_engine=self.engine, verbose=False)
        with self.engine.connect() as conn:
            fk_status = conn.execute(text("PRAGMA foreign_keys")).scalar()
            self.assertEqual(fk_status, 1)

    def test_foreign_key_constraint_blocks_orphaned_records(self):
        """Verify that inserting a record with a non-existent foreign key raises IntegrityError."""
        init_db(target_engine=self.engine, verbose=False)
        session: Session = self.Session()
        try:
            # Attempt to insert medication for non-existent patient ID 999999
            orphan_med = Medication(
                patient_id=999999,
                drug_name="Test Drug",
                status="active",
            )
            session.add(orphan_med)
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
