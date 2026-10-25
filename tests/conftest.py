"""
Test-session isolation: the test suite must never touch the real ./aegis_rx.db* files.

Three independent layers (any one of them is enough to protect the database; together they also
make accidental regressions visible):

1. REDIRECT   - before any application module is imported, AEGIS_DATABASE_URL and
                AEGIS_AUDIT_DB_PATH are forced to a throw-away temp directory. Both `database.database`
                and `app.dependencies` read them at import time, so nothing opens ./aegis_rx.db.
                (Forced, not setdefault: a developer shell that points at a real DB cannot leak in.)
2. GUARD      - sqlite3.connect / sqlite3.dbapi2.connect raise RuntimeError for any attempt to open a
                protected production file (read-only `mode=ro` URIs are allowed). Attempts are recorded.
3. TRIPWIRE   - the SHA-256 of every protected file is taken at session start and compared at the end;
                a change, a creation, a deletion, or any blocked attempt fails the session. This also
                covers code the guard cannot see (e.g. subprocesses, which inherit the redirected env).

Protected files are resolved relative to the repository root, not the working directory.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import re
import shutil
import sqlite3
import sqlite3.dbapi2
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_NAMES = (
    "aegis_rx.db",
    "aegis_rx.db-wal",
    "aegis_rx.db-shm",
    "aegis_rx.db-journal",
    "aegis_rx.db.contaminated-backup",
)


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


PROTECTED = {_norm(REPO_ROOT / name) for name in PROTECTED_NAMES}


# --- 1. REDIRECT -------------------------------------------------------------------------------
_TMP_DIR = Path(tempfile.mkdtemp(prefix="aegis_pytest_"))
atexit.register(shutil.rmtree, _TMP_DIR, ignore_errors=True)
os.environ["AEGIS_DATABASE_URL"] = f"sqlite:///{(_TMP_DIR / 'aegis_test.db').as_posix()}"
os.environ["AEGIS_AUDIT_DB_PATH"] = str(_TMP_DIR / "aegis_test_audit.db")


# --- 2. GUARD ----------------------------------------------------------------------------------
_BLOCKED: list = []


def _opened_path(database):
    """Return (normalised path, read_only) for a sqlite3.connect() target, or (None, False)."""
    if isinstance(database, bytes):
        database = os.fsdecode(database)
    elif isinstance(database, os.PathLike):
        database = os.fspath(database)
    if not isinstance(database, str) or database in ("", ":memory:"):
        return None, False
    if database.startswith("file:"):
        parsed = urlparse(database)
        path = unquote(parsed.path or parsed.netloc)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        return _norm(path), "mode=ro" in parsed.query.split("&")
    return _norm(database), False


def _wrap_connect(real_connect):
    def guarded_connect(*args, **kwargs):
        database = args[0] if args else kwargs.get("database")
        target, read_only = _opened_path(database)
        if target in PROTECTED and not read_only:
            _BLOCKED.append(target)
            raise RuntimeError(
                f"Blocked: tests must not open the production database file {target}. "
                "Use a temp database (see tests/conftest.py)."
            )
        return real_connect(*args, **kwargs)

    guarded_connect.__wrapped__ = real_connect
    return guarded_connect


if not hasattr(sqlite3.connect, "__wrapped__"):
    _real = sqlite3.connect
    sqlite3.connect = _wrap_connect(_real)
    # SQLAlchemy's pysqlite dialect calls sqlite3.dbapi2.connect, a separate module attribute.
    sqlite3.dbapi2.connect = _wrap_connect(sqlite3.dbapi2.connect)


# --- 3. TRIPWIRE -------------------------------------------------------------------------------
def _fingerprint() -> dict:
    out = {}
    for name in PROTECTED_NAMES:
        p = REPO_ROOT / name
        if not p.exists():
            out[name] = None
            continue
        digest = hashlib.sha256()
        with open(p, "rb") as fh:  # plain file read: never opens the file as a database
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        out[name] = (p.stat().st_size, digest.hexdigest())
    return out


_BEFORE = _fingerprint()


@pytest.fixture
def blocked_db_attempts():
    """For tests that deliberately trigger the guard: exposes the attempts and removes them afterwards."""
    start = len(_BLOCKED)
    yield _BLOCKED
    del _BLOCKED[start:]


@pytest.fixture(scope="session", autouse=True)
def _production_db_tripwire():
    yield
    after = _fingerprint()
    changed = sorted(name for name in PROTECTED_NAMES if _BEFORE[name] != after[name])
    problems = []
    if changed:
        problems.append(f"production database files changed during the test session: {changed}")
    if _BLOCKED:
        problems.append(f"{len(_BLOCKED)} attempt(s) to open a production database file were blocked")
    if problems:
        pytest.fail("PRODUCTION DB ISOLATION VIOLATED: " + "; ".join(problems), pytrace=False)
