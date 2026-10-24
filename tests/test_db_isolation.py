"""Proves the test-session isolation in tests/conftest.py actually protects ./aegis_rx.db*."""

import os
import sqlite3
import sqlite3.dbapi2
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_NAMES = (
    "aegis_rx.db",
    "aegis_rx.db-wal",
    "aegis_rx.db-shm",
    "aegis_rx.db-journal",
    "aegis_rx.db.contaminated-backup",
)


def _same(a, b) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def test_environment_is_redirected_away_from_production():
    url = os.environ["AEGIS_DATABASE_URL"]
    audit = os.environ["AEGIS_AUDIT_DB_PATH"]
    assert "aegis_pytest_" in url and "aegis_pytest_" in audit
    for name in PROTECTED_NAMES:
        assert not _same(audit, REPO_ROOT / name)


def test_sqlalchemy_engine_does_not_point_at_production():
    from database.database import DATABASE_URL, engine

    assert DATABASE_URL == os.environ["AEGIS_DATABASE_URL"]
    assert not _same(engine.url.database, REPO_ROOT / "aegis_rx.db")


def test_default_audit_ledger_does_not_point_at_production():
    from app.dependencies import _default_audit_ledger

    for name in PROTECTED_NAMES:
        assert not _same(_default_audit_ledger.db_path, REPO_ROOT / name)


@pytest.mark.parametrize("name", PROTECTED_NAMES)
def test_guard_blocks_stdlib_and_dbapi_connect(name, blocked_db_attempts):
    target = REPO_ROOT / name
    existed = target.exists()
    for connect in (sqlite3.connect, sqlite3.dbapi2.connect):
        with pytest.raises(RuntimeError, match="production database"):
            connect(str(target))
        with pytest.raises(RuntimeError, match="production database"):
            connect(target)
        with pytest.raises(RuntimeError, match="production database"):
            connect(f"file:{target.as_posix()}?mode=rwc", uri=True)
    assert len(blocked_db_attempts) >= 6
    assert target.exists() == existed  # the guard fired before anything could be created


def test_guard_blocks_relative_path_from_repo_root(monkeypatch, blocked_db_attempts):
    monkeypatch.chdir(REPO_ROOT)
    with pytest.raises(RuntimeError, match="production database"):
        sqlite3.connect("aegis_rx.db")


def test_guard_allows_temp_memory_and_readonly_uri(tmp_path):
    sqlite3.connect(":memory:").close()
    db = tmp_path / "scratch.db"
    sqlite3.connect(db).close()
    assert db.exists()
    # read-only URIs stay allowed (the hygiene test inspects tracked DBs this way)
    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.close()
    # a same-named file outside the repo root is NOT production
    lookalike = tmp_path / "aegis_rx.db"
    sqlite3.connect(lookalike).close()
    assert lookalike.exists()
