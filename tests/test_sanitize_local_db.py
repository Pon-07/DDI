import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from sanitize_local_db import sanitize  # noqa: E402


def make_db(path: Path):
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, phone_number TEXT, totp_secret TEXT)")
    c.execute("CREATE TABLE user_sessions(id INTEGER PRIMARY KEY, session_token TEXT)")
    c.execute("CREATE TABLE otp_records(id INTEGER PRIMARY KEY, otp_hash TEXT)")
    c.execute("CREATE TABLE drugs(id INTEGER PRIMARY KEY, name TEXT)")
    c.execute("INSERT INTO users VALUES (1,'+15550000000','SYNTHETICSECRETSYNTHETICSECRET00')")
    c.execute("INSERT INTO user_sessions VALUES (1,'synthetic-token')")
    c.execute("INSERT INTO otp_records VALUES (1,'synthetichash')")
    c.execute("INSERT INTO drugs VALUES (1,'fakedrugol')")
    c.commit()
    return c  # connection stays open so rows can sit only in the -wal file


def test_sensitive_rows_removed_and_knowledge_kept(tmp_path):
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    conn = make_db(src)
    removed = sanitize(src, dst)
    conn.close()
    assert removed == {"user_sessions": 1, "otp_records": 1, "users": 1}
    out = sqlite3.connect(dst)
    assert out.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert out.execute("SELECT COUNT(*) FROM drugs").fetchone()[0] == 1
    out.close()
    # the secret must not survive anywhere in the output bytes (freed pages included)
    assert b"SYNTHETICSECRET" not in dst.read_bytes()
    assert not list(tmp_path.glob("dst.db-*"))


def test_source_is_not_modified(tmp_path):
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    conn = make_db(src)
    conn.close()
    before = src.read_bytes()
    sanitize(src, dst)
    assert src.read_bytes() == before


def test_refuses_to_overwrite_or_use_same_path(tmp_path):
    src = tmp_path / "src.db"
    make_db(src).close()
    with pytest.raises(ValueError):
        sanitize(src, src)
    (tmp_path / "exists.db").write_bytes(b"x")
    with pytest.raises(FileExistsError):
        sanitize(src, tmp_path / "exists.db")
