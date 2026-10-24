"""
Repository hygiene gates: requirements encoding, secrets, and tracked database files.
If `test_no_database_files_are_tracked` fails, run `bash scripts/repo_hygiene.sh`.
"""

import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from tests.data_quality.conftest import REPO_ROOT


def _tracked():
    if not shutil.which("git") or not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
    return [REPO_ROOT / p for p in out.decode().split("\0") if p and (REPO_ROOT / p).exists()]


@pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt"])
def test_requirements_are_plain_utf8(name):
    raw = (REPO_ROOT / name).read_bytes()
    assert not raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")), "BOM found"
    assert b"\x00" not in raw, "NUL bytes (UTF-16?) found"
    raw.decode("utf-8")
    assert b"\r" not in raw


def test_requirements_declare_every_third_party_runtime_import():
    reqs = (REPO_ROOT / "requirements.txt").read_text().lower()
    for pkg in ("fastapi", "sqlalchemy", "pandas", "pydantic"):
        assert re.search(rf"^{pkg}==", reqs, re.M), f"{pkg} not pinned in requirements.txt"


def test_authentication_dependencies_are_absent():
    reqs = (REPO_ROOT / "requirements.txt").read_text().lower()
    for pkg in ("pyotp", "qrcode", "pillow"):
        assert not re.search(rf"^{pkg}==", reqs, re.M), f"authentication-only dependency remains: {pkg}"
    assert not list((REPO_ROOT / "engine" / "auth").rglob("*.py")) if (REPO_ROOT / "engine" / "auth").exists() else True


def test_requirements_pins_are_exact():
    for line in (REPO_ROOT / "requirements.txt").read_text().splitlines():
        assert re.match(r"^[A-Za-z0-9_.\-]+(\[[a-z,]+\])?==[0-9A-Za-z.\-+]+$", line), line


def test_gitignore_blocks_database_files():
    gi = (REPO_ROOT / ".gitignore").read_text()
    for pat in ("*.db", "*.db-wal", "*.db-shm", "staging/"):
        assert pat in gi


def test_no_database_files_are_tracked():
    bad = [str(p.relative_to(REPO_ROOT)) for p in _tracked()
           if re.search(r"\.(db|db-wal|db-shm|db-journal|sqlite3?)$|contaminated-backup", p.name)]
    assert bad == [], f"tracked DB files (run scripts/repo_hygiene.sh): {bad}"


SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bAC[0-9a-f]{32}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(auth[_-]?token|api[_-]?key|secret[_-]?key|password)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
]


def test_no_hardcoded_secrets_in_tracked_text_files():
    hits = []
    for p in _tracked():
        if p.suffix in {".db", ".csv", ".json", ".png", ".zip"} or p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                hits.append(f"{p.relative_to(REPO_ROOT)} :: {pat.pattern[:30]}")
    assert hits == [], hits


def test_tracked_sqlite_files_hold_no_credentials(tmp_path):
    # Inspect COPIES (database + its -wal): even a read-only open of a WAL database can rewrite its -shm file,
    # and these tests must never touch the real files.
    for n, p in enumerate(_tracked()):
        with open(p, "rb") as fh:
            if fh.read(16) != b"SQLite format 3\x00":
                continue
        work = tmp_path / str(n)
        work.mkdir()
        copy = work / "copy.db"
        shutil.copy2(p, copy)
        wal = p.with_name(p.name + "-wal")
        if wal.exists():
            shutil.copy2(wal, work / "copy.db-wal")
        conn = sqlite3.connect(copy)
        try:
            tabs = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert not {"users", "user_sessions", "otp_records"} & tabs
        finally:
            conn.close()
