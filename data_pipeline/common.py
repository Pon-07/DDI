"""Shared helpers: hashing, source discovery, production-DB safety guards."""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

# Files that make up the PRODUCTION database. The pipeline must never write to them.
PRODUCTION_DB_NAMES = (
    "aegis_rx.db",
    "aegis_rx.db-wal",
    "aegis_rx.db-shm",
    "aegis_rx.db-journal",
)
LEGACY_BACKUP_NAME = "aegis_rx.db.contaminated-backup"

# DDInter publishes one CSV per ATC top-level category.
DDINTER_EXPECTED_CODES = ("A", "B", "D", "H", "L", "P", "R", "V")

LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_lfs_pointer(path: Path) -> bool:
    """True when the file is a Git-LFS pointer stub rather than real content."""
    try:
        with open(path, "rb") as fh:
            return fh.read(len(LFS_POINTER_PREFIX)) == LFS_POINTER_PREFIX
    except OSError:
        return False


def production_fingerprints(repo_root: Path) -> Dict[str, Optional[str]]:
    """SHA-256 of each production DB file (None when absent). Used to prove they are untouched."""
    out: Dict[str, Optional[str]] = {}
    for name in PRODUCTION_DB_NAMES + (LEGACY_BACKUP_NAME,):
        p = repo_root / name
        out[name] = sha256_file(p) if p.exists() else None
    return out


def assert_safe_staging_path(repo_root: Path, staging_db: Path) -> None:
    """Refuse to build staging data over any production/backup database file."""
    target = staging_db.resolve()
    for name in PRODUCTION_DB_NAMES + (LEGACY_BACKUP_NAME,):
        if target == (repo_root / name).resolve():
            raise RuntimeError(f"Refusing to use production/backup file as staging DB: {target}")
    if target.parent.resolve() == repo_root.resolve() and target.name.startswith("aegis_rx"):
        raise RuntimeError(f"Staging DB name collides with production naming: {target.name}")


def open_copy_readonly(db_path: Path) -> Optional[tuple]:
    """
    Copy ``db_path`` (plus its -wal/-shm siblings) into a temp dir and open the COPY.

    Opening a WAL-mode database can rewrite -shm/-wal files, and ``immutable=1`` would
    ignore the WAL contents. Querying a copy sidesteps both problems without touching
    the original. Returns (connection, tempdir) or None if the file does not exist.
    """
    if not db_path.exists():
        return None
    tmp = Path(tempfile.mkdtemp(prefix="aegis_dbcopy_"))
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db_path) + suffix)
        if src.exists():
            shutil.copy2(src, tmp / (db_path.name + suffix))
    conn = sqlite3.connect(tmp / db_path.name)
    conn.row_factory = sqlite3.Row
    return conn, tmp


def discover_ddinter_files(data_dir: Path) -> List[Path]:
    return sorted(data_dir.glob("ddinter_downloads_code_*.csv"))


def ddinter_code(path: Path) -> Optional[str]:
    m = re.search(r"code_([A-Za-z])\.csv$", path.name)
    return m.group(1).upper() if m else None


def chunked(seq: Iterable, size: int):
    buf = []
    for item in seq:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf
