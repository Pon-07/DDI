#!/usr/bin/env python3
"""Rotate compromised authentication data in an AEGIS Rx SQLite database.

Why: a snapshot of aegis_rx.db was committed to a public repository. It held TOTP secrets, session tokens and
one-time-code records. Those secrets were also hardcoded in engine/auth/config.py (now removed). Removing them
from the code does not change the values stored in a database that already holds them, so this script does.

Safe by design
  * DRY RUN by default: it only reports counts. Nothing is changed without --apply.
  * --apply makes a verified backup FIRST (SQLite online-backup API, WAL-safe) and refuses to continue
    if the backup cannot be verified, or if it would be written inside a git working tree.
  * It refuses if the database is locked by another process (stop the app first).
  * One transaction: either everything below happens or nothing does.
  * It NEVER prints a secret, token, phone number, name or user id. Only aggregate counts.
  * Standard library only; it does not import the application.

What --apply changes
  * DELETE every row of user_sessions and otp_records (everyone is signed out).
  * For users whose TOTP secret is compromised: totp_secret=NULL, totp_enabled=0, totp_last_verified_at=NULL,
    totp_last_timestep=NULL.  "Compromised" = matches the known leaked values (stored here only as SHA-256
    hashes) or any secret found in a snapshot you pass with --leaked-db.  --scope all clears every user instead.

After --apply, start the app. The first authentication request re-seeds the demo personas with NEW random
secrets (engine/auth/service.py: seed_demo_users). Then run --verify.

    python scripts/rotate_auth_credentials.py --db aegis_rx.db                      # dry run
    python scripts/rotate_auth_credentials.py --db aegis_rx.db --backup-dir D --apply
    python scripts/rotate_auth_credentials.py --db aegis_rx.db --verify             # read-only check

Exit codes: 0 ok | 1 compromised data remains / failure | 2 refused (unsafe or invalid invocation).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

# SHA-256 of the four TOTP secrets that were hardcoded in source and present in the leaked snapshot.
# Hashes only: the secrets themselves are deliberately not stored in this file.
LEGACY_SECRET_SHA256 = frozenset({
    "3a53d0a064baf78cea93be533489094bf896fb7644d9331619ec25b927e7ce27",
    "7648a8aa01c7138fd98a9025426ce965ed50a23ae7435dc95e31fc8e3a61e8d7",
    "cc93cccf741bbb0dbcecf5aba1cb0a49a52c84e7b2167fe3837d111fae332cbf",
    "b732f6fe8ae7343acfb2dbdd7df5df8d985a104e4fef0ee9117d2867ef1858e4",
})

EXIT_OK, EXIT_PROBLEM, EXIT_REFUSED = 0, 1, 2


class Refused(Exception):
    """An unsafe or invalid invocation; nothing was changed."""


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _connect(path: Path, mode: str) -> sqlite3.Connection:
    """mode: 'ro' or 'rw'. Never creates a missing database file."""
    uri = Path(path).resolve().as_uri() + f"?mode={mode}"
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    conn.isolation_level = None  # explicit transaction control
    return conn


def _tables(conn: sqlite3.Connection) -> Set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


_REQUIRED_COLUMNS = {
    "users": {"id", "totp_secret", "totp_enabled", "totp_last_verified_at", "totp_last_timestep"},
    "user_sessions": {"id", "session_token", "user_id"},
    "otp_records": {"id", "phone_number", "otp_hash"},
}


def _columns(conn: sqlite3.Connection, table: str) -> Set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _validate_schema(conn: sqlite3.Connection) -> Set[str]:
    tables = _tables(conn)
    missing_tables = set(_REQUIRED_COLUMNS) - tables
    if missing_tables:
        raise Refused("Database is missing required AEGIS auth tables: " + ", ".join(sorted(missing_tables)))
    for table, required in _REQUIRED_COLUMNS.items():
        missing = required - _columns(conn, table)
        if missing:
            raise Refused(f"Database table '{table}' is missing required columns: " + ", ".join(sorted(missing)))
    return tables


def _count(conn: sqlite3.Connection, table: str, tables: Set[str]) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table in tables else 0


def _secrets(conn: sqlite3.Connection, tables: Set[str]) -> List[Tuple[int, str]]:
    if "users" not in tables or not {"id", "totp_secret"} <= _columns(conn, "users"):
        raise Refused("The 'users' table has no totp_secret column; this does not look like an AEGIS Rx database.")
    return [(i, s) for i, s in conn.execute("SELECT id, totp_secret FROM users") if s]


def _compromised_hashes(leaked_dbs: Iterable[Path]) -> Set[str]:
    hashes = set(LEGACY_SECRET_SHA256)
    for p in leaked_dbs:
        if not Path(p).is_file():
            raise Refused(f"--leaked-db file not found: {p}")
        conn = _connect(Path(p), "ro")
        try:
            hashes.update(_sha(s) for _, s in _secrets(conn, _tables(conn)))
        except sqlite3.DatabaseError as exc:
            raise Refused(f"Could not read --leaked-db {p}: {exc}")
        finally:
            conn.close()
    return hashes


def _inside_git_worktree(path: Path) -> bool:
    p = path.resolve()
    return any((parent / ".git").exists() for parent in [p, *p.parents])


def _plan(conn: sqlite3.Connection, hashes: Set[str], scope: str) -> dict:
    tables = _tables(conn)
    with_secret = _secrets(conn, tables)
    targets = [uid for uid, s in with_secret if scope == "all" or _sha(s) in hashes]
    return {
        "users": _count(conn, "users", tables),
        "users_with_secret": len(with_secret),
        "compromised": sum(1 for _, s in with_secret if _sha(s) in hashes),
        "target_ids": targets,
        "sessions": _count(conn, "user_sessions", tables),
        "otps": _count(conn, "otp_records", tables),
        "tables": tables,
    }


def _report(plan: dict, scope: str) -> None:
    print(f"  users: {plan['users']} | with a TOTP secret: {plan['users_with_secret']}")
    print(f"  secrets matching known-compromised values: {plan['compromised']}")
    print(f"  users whose TOTP would be cleared (scope={scope}): {len(plan['target_ids'])}")
    print(f"  sessions to delete: {plan['sessions']} | one-time-code records to delete: {plan['otps']}")


def _integrity_ok(conn: sqlite3.Connection) -> bool:
    return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def _schema_signature(conn: sqlite3.Connection) -> List[Tuple[str, str, str, Optional[str]]]:
    return conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name, tbl_name, sql"
    ).fetchall()


def _rows_match(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> bool:
    quote = lambda value: '"' + value.replace('"', '""') + '"'
    table_sql = quote(table)
    columns = [row[1] for row in src.execute(f"PRAGMA table_info({table_sql})")]
    if not columns:
        return False
    order_by = ", ".join(quote(column) for column in columns)
    sql = f"SELECT * FROM {table_sql} ORDER BY {order_by}"
    left, right = src.execute(sql), dst.execute(sql)
    while True:
        a, b = left.fetchmany(512), right.fetchmany(512)
        if a != b:
            return False
        if not a:
            return True


def _databases_match(src: sqlite3.Connection, dst: sqlite3.Connection) -> bool:
    tables = _tables(src)
    if (tables != _tables(dst) or _schema_signature(src) != _schema_signature(dst)
            or not _integrity_ok(dst)):
        return False
    return all(_rows_match(src, dst, table) for table in sorted(tables))


def _backup(src: sqlite3.Connection, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"aegis_rx_pre_rotation_{stamp}.db"
    n = 1
    while dest.exists():  # never overwrite an earlier backup
        n += 1
        dest = backup_dir / f"aegis_rx_pre_rotation_{stamp}_{n}.db"
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
    check = _connect(dest, "ro")
    try:
        if not _databases_match(src, check):
            raise Refused(f"Backup verification failed; nothing was changed. Remove {dest} and investigate.")
    finally:
        check.close()
    return dest


def run(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=Path("aegis_rx.db"), help="SQLite database file (default: aegis_rx.db)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="make the changes (default is a dry run)")
    mode.add_argument("--verify", action="store_true", help="read-only: fail if a compromised secret is still present")
    ap.add_argument("--scope", choices=("compromised", "all"), default="compromised",
                    help="'compromised' (default) clears only leaked secrets; 'all' clears every user's TOTP secret")
    ap.add_argument("--leaked-db", type=Path, action="append", default=[],
                    help="a database snapshot whose TOTP secrets must also be treated as compromised (repeatable)")
    ap.add_argument("--backup-dir", type=Path,
                    default=Path.home() / f"aegis-db-backup-{_dt.date.today().isoformat()}",
                    help="where the pre-rotation backup goes; must be OUTSIDE any git working tree")
    args = ap.parse_args(argv)

    try:
        if not args.db.is_file():
            raise Refused(f"Database file not found: {args.db}")
        hashes = _compromised_hashes(args.leaked_db)

        if args.verify:
            conn = _connect(args.db, "ro")
            try:
                tables = _validate_schema(conn)
                bad = sum(1 for _, s in _secrets(conn, tables) if _sha(s) in hashes)
                print("VERIFY (read-only)")
                print(f"  users: {_count(conn, 'users', tables)} | sessions: {_count(conn, 'user_sessions', tables)} "
                      f"| one-time-code records: {_count(conn, 'otp_records', tables)}")
                print(f"  compromised TOTP secrets still present: {bad} (must be 0)")
            finally:
                conn.close()
            return EXIT_OK if bad == 0 else EXIT_PROBLEM

        if not args.apply:
            conn = _connect(args.db, "ro")
            try:
                print("DRY RUN - nothing will be changed. Add --apply to rotate.")
                _validate_schema(conn)
                _report(_plan(conn, hashes, args.scope), args.scope)
            finally:
                conn.close()
            return EXIT_OK

        # ---- --apply ----------------------------------------------------------------------------------
        if _inside_git_worktree(args.backup_dir):
            raise Refused(f"Backup dir {args.backup_dir} is inside a git working tree. The backup holds the old "
                          "secrets; choose a folder outside the repository.")
        conn = _connect(args.db, "rw")
        try:
            try:  # is anyone else writing? (the app must be stopped)
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError as exc:
                raise Refused(f"Database is busy/locked ({exc}). Stop the application, then retry.")
            _validate_schema(conn)
            plan = _plan(conn, hashes, args.scope)
            print("APPLY")
            _report(plan, args.scope)
            if not _integrity_ok(conn):
                raise Refused("The database fails PRAGMA integrity_check; refusing to modify it.")
            backup = _backup(conn, args.backup_dir)
            print(f"  backup written and verified: {backup}")
            try:
                conn.execute("BEGIN IMMEDIATE")
                backup_check = _connect(backup, "ro")
                try:
                    if not _databases_match(conn, backup_check):
                        conn.execute("ROLLBACK")
                        print("REFUSED: the database changed after backup verification; no changes were made.")
                        return EXIT_REFUSED
                finally:
                    backup_check.close()
                locked_plan = _plan(conn, hashes, args.scope)
                if locked_plan != plan:
                    conn.execute("ROLLBACK")
                    print("REFUSED: the rotation plan changed before the write lock; no changes were made.")
                    return EXIT_REFUSED
                plan = locked_plan
                tables = plan["tables"]
                if "user_sessions" in tables:
                    conn.execute("DELETE FROM user_sessions")
                if "otp_records" in tables:
                    conn.execute("DELETE FROM otp_records")
                if plan["target_ids"]:
                    conn.executemany(
                        "UPDATE users SET totp_secret=NULL, totp_enabled=0, "
                        "totp_last_verified_at=NULL, totp_last_timestep=NULL WHERE id=?",
                        [(i,) for i in plan["target_ids"]])
                conn.execute("COMMIT")
            except Exception as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                print(f"FAILED and rolled back ({exc}). Your backup is at {backup}.")
                return EXIT_PROBLEM
            busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
            after = _plan(conn, hashes, args.scope)
            ok = (after["compromised"] == 0 and after["sessions"] == 0 and after["otps"] == 0
                  and (args.scope != "all" or after["users_with_secret"] == 0) and _integrity_ok(conn))
            print("RESULT")
            print(f"  compromised secrets remaining: {after['compromised']} | sessions: {after['sessions']} "
                  f"| one-time-code records: {after['otps']} | integrity_check: ok")
            if busy:
                print("  WARNING: another process still has the database open. Make sure the app is stopped.")
            print("  Next: start the app (it re-seeds demo personas with NEW random secrets), run --verify, "
                  "then delete the backup - it still contains the old secrets and personal data.")
            return EXIT_OK if ok else EXIT_PROBLEM
        finally:
            conn.close()
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return EXIT_REFUSED
    except sqlite3.DatabaseError as exc:
        print(f"REFUSED: could not read the database ({exc}).")
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(run())
