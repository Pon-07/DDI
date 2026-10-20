"""Tests for scripts/rotate_auth_credentials.py. Synthetic data only: secrets are random per test run and the
script's built-in list of leaked-secret hashes is patched, so no real secret appears in this file."""
import base64
import hashlib
import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rotate_auth_credentials.py"
spec = importlib.util.spec_from_file_location("rotate_auth_credentials", SCRIPT)
rot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rot)


def _secret() -> str:
    return base64.b32encode(os.urandom(20)).decode()


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A temp DB with the REAL auth schema: 2 'leaked' users, 1 unaffected user, 1 user without a secret."""
    leaked = [_secret(), _secret()]
    safe = _secret()
    monkeypatch.setattr(rot, "LEGACY_SECRET_SHA256", frozenset(_sha(s) for s in leaked))
    db = tmp_path / "work" / "aegis_test.db"
    db.parent.mkdir()
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, phone_number TEXT, full_name TEXT, role TEXT,
            is_active INTEGER, demo_mode_allowed INTEGER, totp_secret TEXT,
            totp_enabled INTEGER, totp_last_verified_at TEXT, totp_last_timestep INTEGER,
            created_at TEXT
        );
        CREATE TABLE user_sessions (
            id INTEGER PRIMARY KEY, session_token TEXT, user_id INTEGER, role TEXT,
            phone_number TEXT, created_at TEXT, expires_at TEXT, last_activity_at TEXT,
            is_active INTEGER
        );
        CREATE TABLE otp_records (
            id INTEGER PRIMARY KEY, phone_number TEXT, otp_hash TEXT, provider TEXT,
            is_demo INTEGER, attempts_remaining INTEGER, is_used INTEGER,
            created_at TEXT, expires_at TEXT
        );
    """)
    c.execute("PRAGMA journal_mode=WAL")
    rows = [(1, "+10000000001", "A One", "Doctor", leaked[0]), (2, "+10000000002", "B Two", "Nurse", leaked[1]),
            (3, "+10000000003", "C Three", "Doctor", safe), (4, "+10000000004", "D Four", "Doctor", None)]
    for uid, phone, name, role, sec in rows:
        c.execute("INSERT INTO users (id, phone_number, full_name, role, is_active, demo_mode_allowed, totp_secret, "
                  "totp_enabled, totp_last_timestep, created_at) VALUES (?,?,?,?,1,1,?,?,?, '2026-01-01')",
                  (uid, phone, name, role, sec, 1 if sec else 0, 12345 if sec else None))
    for i in range(3):
        c.execute("INSERT INTO user_sessions (session_token, user_id, role, phone_number, created_at, expires_at, "
                  "last_activity_at, is_active) VALUES (?,?,?,?,?,?,?,1)",
                  (f"tok{i}-{_secret()}", 1, "Doctor", "+10000000001", "2026-01-01", "2026-01-02", "2026-01-01"))
    for i in range(2):
        c.execute("INSERT INTO otp_records (phone_number, otp_hash, provider, is_demo, attempts_remaining, is_used, "
                  "created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                  ("+10000000001", _sha(str(i)), "local", 0, 5, 0, "2026-01-01", "2026-01-01"))
    c.commit()
    c.close()
    return {"db": db, "leaked": leaked, "safe": safe, "backup": tmp_path / "backups"}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(db: Path, sql: str):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def test_dry_run_changes_nothing_and_creates_no_backup(world, capsys):
    before = _digest(world["db"])
    assert rot.run(["--db", str(world["db"]), "--backup-dir", str(world["backup"])]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "sessions to delete: 3" in out
    assert "users whose TOTP would be cleared (scope=compromised): 2" in out
    assert "[1, 2]" not in out
    assert _digest(world["db"]) == before
    assert not world["backup"].exists()


def test_apply_rotates_only_compromised_and_clears_sessions(world, capsys):
    rc = rot.run(["--db", str(world["db"]), "--backup-dir", str(world["backup"]), "--apply"])
    assert rc == 0, capsys.readouterr().out
    users = {r[0]: r[1:] for r in _rows(world["db"], "SELECT id, totp_secret, totp_enabled, totp_last_timestep FROM users")}
    assert users[1] == (None, 0, None) and users[2] == (None, 0, None)
    assert users[3] == (world["safe"], 1, 12345)          # unaffected user untouched
    assert users[4] == (None, 0, None)
    assert _rows(world["db"], "SELECT COUNT(*) FROM user_sessions") == [(0,)]
    assert _rows(world["db"], "SELECT COUNT(*) FROM otp_records") == [(0,)]


def test_backup_is_a_faithful_restorable_copy(world):
    rot.run(["--db", str(world["db"]), "--backup-dir", str(world["backup"]), "--apply"])
    backups = list(world["backup"].glob("aegis_rx_pre_rotation_*.db"))
    assert len(backups) == 1
    assert _rows(backups[0], "PRAGMA integrity_check") == [("ok",)]
    assert _rows(backups[0], "SELECT COUNT(*) FROM users WHERE totp_secret IS NOT NULL") == [(3,)]
    assert _rows(backups[0], "SELECT COUNT(*) FROM user_sessions") == [(3,)]


def test_scope_all_clears_every_secret(world):
    assert rot.run(["--db", str(world["db"]), "--backup-dir", str(world["backup"]), "--apply", "--scope", "all"]) == 0
    assert _rows(world["db"], "SELECT COUNT(*) FROM users WHERE totp_secret IS NOT NULL") == [(0,)]


def test_apply_is_idempotent(world):
    args = ["--db", str(world["db"]), "--backup-dir", str(world["backup"]), "--apply"]
    assert rot.run(args) == 0
    assert rot.run(args) == 0  # second run: nothing left to rotate, still succeeds (new backup file)
    assert len(list(world["backup"].glob("aegis_rx_pre_rotation_*.db"))) == 2  # earlier backup never overwritten
    assert _rows(world["db"], "SELECT totp_secret FROM users WHERE id=3") == [(world["safe"],)]


def test_verify_fails_before_and_passes_after_and_never_writes(world):
    before = _digest(world["db"])
    assert rot.run(["--db", str(world["db"]), "--verify"]) == 1
    assert _digest(world["db"]) == before
    rot.run(["--db", str(world["db"]), "--backup-dir", str(world["backup"]), "--apply"])
    assert rot.run(["--db", str(world["db"]), "--verify"]) == 0


def test_leaked_db_snapshot_secrets_are_treated_as_compromised(world, tmp_path):
    snap = tmp_path / "snapshot.db"
    c = sqlite3.connect(snap)
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, totp_secret TEXT)")
    c.execute("INSERT INTO users VALUES (1, ?)", (world["safe"],))   # pretend user 3's secret leaked too
    c.commit()
    c.close()
    assert rot.run(["--db", str(world["db"]), "--verify", "--leaked-db", str(snap)]) == 1
    assert rot.run(["--db", str(world["db"]), "--backup-dir", str(world["backup"]), "--apply",
                    "--leaked-db", str(snap)]) == 0
    assert _rows(world["db"], "SELECT totp_secret FROM users WHERE id=3") == [(None,)]


def test_refuses_when_database_is_locked_by_another_writer(world, capsys):
    holder = sqlite3.connect(world["db"], timeout=0.1)
    holder.isolation_level = None
    holder.execute("BEGIN IMMEDIATE")
    try:
        before = _digest(world["db"])
        rc = rot.run(["--db", str(world["db"]), "--backup-dir", str(world["backup"]), "--apply"])
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert rc == 2 and "busy/locked" in capsys.readouterr().out
    assert not world["backup"].exists() and _digest(world["db"]) == before


def test_refuses_backup_inside_a_git_working_tree(world, tmp_path, capsys):
    repo = tmp_path / "somerepo"
    (repo / ".git").mkdir(parents=True)
    rc = rot.run(["--db", str(world["db"]), "--backup-dir", str(repo / "backups"), "--apply"])
    assert rc == 2 and "git working tree" in capsys.readouterr().out
    assert _rows(world["db"], "SELECT COUNT(*) FROM user_sessions") == [(3,)]   # untouched


def test_refuses_missing_or_foreign_database(tmp_path, capsys):
    assert rot.run(["--db", str(tmp_path / "nope.db")]) == 2
    other = tmp_path / "other.db"
    c = sqlite3.connect(other)
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    c.commit()
    c.close()
    assert rot.run(["--db", str(other)]) == 2


def test_output_never_contains_secrets_tokens_or_phone_numbers(world, capsys):
    rot.run(["--db", str(world["db"])])
    rot.run(["--db", str(world["db"]), "--backup-dir", str(world["backup"]), "--apply"])
    rot.run(["--db", str(world["db"]), "--verify"])
    out = capsys.readouterr().out
    for secret in world["leaked"] + [world["safe"]]:
        assert secret not in out
    assert "+1000000" not in out and "tok0-" not in out and "A One" not in out


def test_database_without_auth_tables_is_refused_without_deleting_auth_named_tables(tmp_path):
    db = tmp_path / "unrelated.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE user_sessions (id INTEGER, session_token TEXT, user_id INTEGER)")
    c.execute("CREATE TABLE otp_records (id INTEGER, phone_number TEXT, otp_hash TEXT)")
    c.execute("INSERT INTO user_sessions VALUES (1, 'synthetic-token', 1)")
    c.execute("INSERT INTO otp_records VALUES (1, 'synthetic-phone', 'synthetic-hash')")
    c.commit()
    c.close()

    assert rot.run(["--db", str(db)]) == 2
    assert _rows(db, "SELECT COUNT(*) FROM user_sessions") == [(1,)]
    assert _rows(db, "SELECT COUNT(*) FROM otp_records") == [(1,)]


def test_apply_rolls_back_after_mid_transaction_failure(world, capsys):
    c = sqlite3.connect(world["db"])
    c.execute("CREATE TRIGGER fail_otp_delete BEFORE DELETE ON otp_records "
              "BEGIN SELECT RAISE(ABORT, 'synthetic rollback test'); END")
    c.commit()
    c.close()

    rc = rot.run(["--db", str(world["db"]), "--backup-dir", str(world["backup"]), "--apply"])
    out = capsys.readouterr().out
    assert rc == 1 and "FAILED and rolled back" in out
    assert _rows(world["db"], "SELECT COUNT(*) FROM user_sessions") == [(3,)]
    assert _rows(world["db"], "SELECT COUNT(*) FROM otp_records") == [(2,)]
    assert _rows(world["db"], "SELECT totp_secret FROM users WHERE id=1") == [(world["leaked"][0],)]
    assert len(list(world["backup"].glob("aegis_rx_pre_rotation_*.db"))) == 1


def test_backup_verification_compares_schema_and_all_rows(world):
    source = sqlite3.connect(world["db"])
    world["backup"].mkdir(parents=True, exist_ok=True)
    backup = world["backup"] / "verified.db"
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
        assert rot._integrity_ok(target)
        assert rot._schema_signature(source) == rot._schema_signature(target)
        assert rot._databases_match(source, target)
        for table in rot._tables(source):
            assert rot._rows_match(source, target, table)
    finally:
        target.close()
        source.close()


@pytest.mark.parametrize(
    "table,column",
    [(table, column) for table, columns in rot._REQUIRED_COLUMNS.items() for column in sorted(columns)],
)
def test_refuses_each_missing_required_column(tmp_path, table, column):
    db = tmp_path / f"missing_{table}_{column}.db"
    c = sqlite3.connect(db)
    for current, required in rot._REQUIRED_COLUMNS.items():
        columns = sorted(required - ({column} if current == table else set()))
        declaration = ", ".join(f'"{name}" TEXT' for name in columns)
        c.execute(f'CREATE TABLE "{current}" ({declaration})')
    c.close()
    assert rot.run(["--db", str(db)]) == 2


def test_apply_refuses_if_database_changes_after_backup(world, monkeypatch, capsys):
    real_backup = rot._backup

    def backup_then_add_session(source, backup_dir):
        backup = real_backup(source, backup_dir)
        c = sqlite3.connect(world["db"])
        c.execute(
            "INSERT INTO user_sessions "
            "(session_token, user_id, role, phone_number, created_at, expires_at, last_activity_at, is_active) "
            "VALUES ('synthetic-new-session', 1, 'Doctor', 'synthetic-phone', 'now', 'later', 'now', 1)"
        )
        c.commit()
        c.close()
        return backup

    monkeypatch.setattr(rot, "_backup", backup_then_add_session)
    rc = rot.run(["--db", str(world["db"]), "--backup-dir", str(world["backup"]), "--apply"])
    assert rc == 2 and "changed after backup verification" in capsys.readouterr().out
    assert _rows(world["db"], "SELECT COUNT(*) FROM user_sessions") == [(4,)]
    assert _rows(world["db"], "SELECT COUNT(*) FROM otp_records") == [(2,)]
    assert _rows(world["db"], "SELECT totp_secret FROM users WHERE id=1") == [(world["leaked"][0],)]


def test_apply_refuses_if_compromised_secret_changes_between_plan_and_backup(world, monkeypatch, capsys):
    real_backup = rot._backup

    def backup_after_adding_compromised_secret(source, backup_dir):
        c = sqlite3.connect(world["db"])
        c.execute("UPDATE users SET totp_secret=? WHERE id=3", (world["leaked"][0],))
        c.commit()
        c.close()
        return real_backup(source, backup_dir)

    monkeypatch.setattr(rot, "_backup", backup_after_adding_compromised_secret)
    rc = rot.run(["--db", str(world["db"]), "--backup-dir", str(world["backup"]), "--apply"])
    out = capsys.readouterr().out
    assert rc == 2 and "rotation plan changed" in out
    assert _rows(world["db"], "SELECT totp_secret FROM users WHERE id=1") == [(world["leaked"][0],)]
    assert _rows(world["db"], "SELECT totp_secret FROM users WHERE id=3") == [(world["leaked"][0],)]
    assert _rows(world["db"], "SELECT COUNT(*) FROM user_sessions") == [(3,)]
    assert _rows(world["db"], "SELECT COUNT(*) FROM otp_records") == [(2,)]
    assert len(list(world["backup"].glob("aegis_rx_pre_rotation_*.db"))) == 1
