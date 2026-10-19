"""
Create a SANITISED copy of an AEGIS Rx SQLite database that is safe to share.

* Takes a consistent snapshot with SQLite's backup API (includes -wal contents).
* Empties users / user_sessions / otp_records and clears every TOTP field.
* Leaves knowledge tables and synthetic demo clinical rows untouched.
* Never modifies the source and refuses to write over it.

    python scripts/sanitize_local_db.py --src aegis_rx.db --dst /tmp/aegis_rx.sanitized.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional

SENSITIVE_TABLES = ("user_sessions", "otp_records", "users")


def sanitize(src: Path, dst: Path) -> Dict[str, int]:
    src, dst = src.resolve(), dst.resolve()
    if not src.exists():
        raise FileNotFoundError(src)
    if src == dst:
        raise ValueError("--dst must differ from --src")
    if dst.exists():
        raise FileExistsError(f"{dst} exists; refusing to overwrite")

    # Open the source read-only so even a WAL-mode database is never modified.
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        source.close()
    removed: Dict[str, int] = {}
    try:
        existing = {r[0] for r in target.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in SENSITIVE_TABLES:
            if table in existing:
                removed[table] = target.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                target.execute(f'DELETE FROM "{table}"')
        target.commit()
        target.execute("PRAGMA journal_mode=DELETE")
        target.execute("VACUUM")  # also drops freed pages that may still hold deleted secrets
    finally:
        target.close()
    return removed


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--dst", required=True, type=Path)
    a = ap.parse_args(argv)
    removed = sanitize(a.src, a.dst)
    print(f"Sanitised copy written to {a.dst}. Rows removed: {removed or 'none (no sensitive tables found)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
