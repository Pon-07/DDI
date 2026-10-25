"""
Regression tests for PostgreSQL and Neon Database Migration.
Validates:
1. Database URL normalization for PostgreSQL drivers (psycopg2, psycopg) and Neon SSL requirements.
2. Dialect-aware engine configuration and pooling settings.
3. Partial index compatibility (postgresql_where and sqlite_where).
4. Cross-platform AuditLedger initialization and cryptographic chain operations.
5. Migration script data type coercion (datetime, date, boolean, json).
"""

from datetime import date, datetime
import json
import unittest
from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

from database.database import (
    DEFAULT_POSTGRES_URL,
    get_engine_args,
    normalize_database_url,
)
from models.models import SubstitutionRequest
from agents.audit.ledger import AuditLedger
from scripts.migrate_sqlite_to_postgres import coerce_value, parse_date, parse_datetime


class TestPostgresNeonMigration(unittest.TestCase):

    def test_database_url_normalization(self):
        """Test URL normalization handles postgres://, bare postgresql://, and preserves Neon query params."""
        # 1. Bare postgres:// prefix
        url1 = "postgres://user:pass@localhost:5432/testdb"
        norm1 = normalize_database_url(url1)
        self.assertTrue(norm1.startswith("postgresql+psycopg2://"))

        # 2. Bare postgresql:// prefix
        url2 = "postgresql://user:pass@localhost:5432/testdb"
        norm2 = normalize_database_url(url2)
        self.assertTrue(norm2.startswith("postgresql+psycopg2://"))

        # 3. Explicit driver is preserved
        url3 = "postgresql+psycopg://user:pass@localhost:5432/testdb"
        norm3 = normalize_database_url(url3)
        self.assertEqual(norm3, url3)

        # 4. Neon connection string with SSL and pooler parameters is preserved
        neon_raw = "postgresql://neondb_owner:npg_123@ep-sample-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
        neon_norm = normalize_database_url(neon_raw)
        self.assertTrue(neon_norm.startswith("postgresql+psycopg2://"))
        self.assertIn("sslmode=require", neon_norm)
        self.assertIn("channel_binding=require", neon_norm)

        # 5. SQLite URLs remain untouched
        sqlite_url = "sqlite:///./aegis_rx.db"
        self.assertEqual(normalize_database_url(sqlite_url), sqlite_url)

    def test_engine_arguments(self):
        """Test dialect-specific engine configuration."""
        pg_args = get_engine_args("postgresql+psycopg2://localhost/db")
        self.assertTrue(pg_args.get("pool_pre_ping"))
        self.assertIn("pool_recycle", pg_args)
        self.assertIn("pool_size", pg_args)
        self.assertNotIn("connect_args", pg_args)

        sqlite_args = get_engine_args("sqlite:///./test.db")
        self.assertIn("connect_args", sqlite_args)
        self.assertFalse(sqlite_args["connect_args"]["check_same_thread"])
        self.assertNotIn("pool_pre_ping", sqlite_args)

    def test_partial_index_has_postgresql_where(self):
        """Verify SubstitutionRequest partial index specifies postgresql_where alongside sqlite_where."""
        indexes = SubstitutionRequest.__table__.indexes
        pending_idx = next((i for i in indexes if i.name == "uq_substitution_pending_order"), None)
        self.assertIsNotNone(pending_idx, "uq_substitution_pending_order index not found")

        # sqlite_where check
        self.assertIn("where", pending_idx.dialect_options["sqlite"])
        self.assertEqual(str(pending_idx.dialect_options["sqlite"]["where"]), "status = 'pending'")

        # postgresql_where check
        self.assertIn("where", pending_idx.dialect_options["postgresql"])
        self.assertEqual(str(pending_idx.dialect_options["postgresql"]["where"]), "status = 'pending'")

    def test_audit_ledger_sqlite_mode(self):
        """Verify AuditLedger maintains complete backwards compatibility in SQLite test mode."""
        ledger = AuditLedger(db_path=":memory:")
        self.assertFalse(ledger.is_postgres)
        self.assertIsNone(ledger.engine)

        rec = ledger.append_event(
            actor="dr_watson",
            event_type="MEDICATION_REVIEWED",
            payload={"patient_id": 42, "drug": "Aspirin"},
        )
        self.assertEqual(rec.id, 1)
        self.assertEqual(rec.actor, "dr_watson")
        self.assertTrue(len(rec.hash) == 64)

        events = ledger.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["drug"], "Aspirin")

        res = ledger.verify_chain()
        self.assertTrue(res.is_valid)

    def test_migration_value_coercion(self):
        """Test SQLite to PostgreSQL data migration type conversions."""
        # Date and DateTime coercion
        dt = parse_datetime("2026-10-25 04:27:54.090276")
        self.assertIsInstance(dt, datetime)
        self.assertEqual(dt.year, 2026)

        d = parse_date("1985-06-15")
        self.assertIsInstance(d, date)
        self.assertEqual(d.year, 1985)

        # Boolean coercion
        self.assertTrue(coerce_value("users", "is_active", 1))
        self.assertFalse(coerce_value("users", "is_active", 0))
        self.assertTrue(coerce_value("users", "totp_enabled", "true"))
        self.assertFalse(coerce_value("users", "totp_enabled", "false"))

        # JSON coercion
        json_str = coerce_value("audit_ledger", "payload", {"key": "value"})
        self.assertEqual(json.loads(json_str), {"key": "value"})

        json_str2 = coerce_value("audit_ledger", "payload", '{"patient": 101}')
        self.assertEqual(json.loads(json_str2), {"patient": 101})


if __name__ == "__main__":
    unittest.main()
