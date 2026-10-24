"""Shared fixtures: one staging build per test session, written to a temp dir (never to production)."""

import sqlite3
from pathlib import Path

import pytest

from data_pipeline.build import run_build

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data_validation"

needs_real_data = pytest.mark.skipif(
    not list(DATA_DIR.glob("ddinter_downloads_code_*.csv")),
    reason="shipped DDInter CSV not present",
)


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("staging_build")
    report = run_build(
        REPO_ROOT,
        staging_db=out / "staging.db",
        clean_dir=out / "clean",
        reports_dir=out / "reports",
        include_legacy=False,
    )
    return {"report": report, "db": out / "staging.db", "clean": out / "clean", "reports": out / "reports",
            "dir": out}


@pytest.fixture()
def staging(built):
    conn = sqlite3.connect(built["db"])
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()
