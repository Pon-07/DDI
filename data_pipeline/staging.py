"""Staging SQLite schema + loaders. Journal mode is DELETE so the DB is a single file (no -wal/-shm)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from data_pipeline.common import chunked
from data_pipeline.ddinter import DDInterResult
from data_pipeline.openfda import SECTIONS

SCHEMA = f"""
CREATE TABLE source_files (
    file_id    INTEGER PRIMARY KEY,
    dataset    TEXT NOT NULL,
    path       TEXT NOT NULL UNIQUE,
    sha256     TEXT,
    size_bytes INTEGER,
    row_count  INTEGER,
    status     TEXT NOT NULL CHECK (status IN ('ingested','missing','lfs_pointer','blocked')),
    note       TEXT
);

-- ===== DDInter: raw (verbatim) -> drugs / pairs (derived) ================================
CREATE TABLE ddinter_raw (
    raw_id            INTEGER PRIMARY KEY,
    file_id           INTEGER NOT NULL REFERENCES source_files(file_id),
    line_no           INTEGER NOT NULL,
    raw_fields_json   TEXT NOT NULL,
    id_a TEXT, drug_a TEXT, id_b TEXT, drug_b TEXT, level TEXT,
    row_sha256        TEXT NOT NULL,
    quarantine_reason TEXT,
    UNIQUE (file_id, line_no)
);
CREATE TABLE ddinter_drugs (
    ddinter_id            TEXT PRIMARY KEY,
    id_num                INTEGER NOT NULL UNIQUE,
    name_original         TEXT NOT NULL,
    name_variants_json    TEXT,
    name_base             TEXT NOT NULL,
    qualifier             TEXT,
    qualifier_kind        TEXT CHECK (qualifier_kind IN ('route_form','salt','other') OR qualifier_kind IS NULL),
    name_normalized       TEXT NOT NULL,
    ingredient_normalized TEXT NOT NULL,
    n_pairs               INTEGER NOT NULL,
    -- ML feature slots: NULL = not available (never guessed)
    rxcui TEXT, atc_codes TEXT, cyp_profile TEXT, drugbank_id TEXT
);
CREATE TABLE ddinter_pairs (
    pair_id              INTEGER PRIMARY KEY,
    id_lo                TEXT NOT NULL REFERENCES ddinter_drugs(ddinter_id),
    id_hi                TEXT NOT NULL REFERENCES ddinter_drugs(ddinter_id),
    num_lo               INTEGER NOT NULL,
    num_hi               INTEGER NOT NULL,
    drug_lo              TEXT NOT NULL,
    drug_hi              TEXT NOT NULL,
    severity_label       TEXT CHECK (severity_label IN ('Major','Moderate','Minor') OR severity_label IS NULL),
    severity_levels_seen TEXT NOT NULL,
    label_status         TEXT NOT NULL CHECK (label_status IN ('labeled','unknown','conflict')),
    resolution           TEXT NOT NULL,
    n_source_rows        INTEGER NOT NULL,
    source_files         TEXT NOT NULL,
    evidence_id          TEXT NOT NULL,
    description          TEXT,
    UNIQUE (id_lo, id_hi),
    CHECK (num_lo < num_hi),
    CHECK ((label_status = 'labeled') = (severity_label IS NOT NULL))
);
CREATE TABLE ddinter_pair_rows (
    raw_id  INTEGER PRIMARY KEY REFERENCES ddinter_raw(raw_id),
    pair_id INTEGER NOT NULL REFERENCES ddinter_pairs(pair_id)
);

-- ===== openFDA ============================================================================
CREATE TABLE openfda_labels (
    label_id          TEXT PRIMARY KEY,
    file_id           INTEGER REFERENCES source_files(file_id),
    source            TEXT NOT NULL,
    brand_name        TEXT NOT NULL,
    generic_name      TEXT NOT NULL,
    name_status       TEXT NOT NULL CHECK (name_status IN ('named','unnamed')),
    primary_name      TEXT,
    multi_product     INTEGER NOT NULL CHECK (multi_product IN (0,1)),
    name_char_len     INTEGER NOT NULL,
    name_normalized   TEXT,
    renal_flag        INTEGER CHECK (renal_flag IN (0,1) OR renal_flag IS NULL),
    hepatic_flag      INTEGER CHECK (hepatic_flag IN (0,1) OR hepatic_flag IS NULL),
    n_sections        INTEGER NOT NULL,
    sections_present  TEXT NOT NULL,
    ingestible        INTEGER NOT NULL CHECK (ingestible IN (0,1)),
    quarantine_reason TEXT,
    rxcui             TEXT,
    CHECK ((name_status = 'named') = (primary_name IS NOT NULL))
);
CREATE TABLE openfda_sections (
    section_id  INTEGER PRIMARY KEY,
    label_id    TEXT NOT NULL REFERENCES openfda_labels(label_id),
    section     TEXT NOT NULL CHECK (section IN ({", ".join("'" + s + "'" for s in SECTIONS)})),
    text        TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    char_len    INTEGER NOT NULL,
    UNIQUE (label_id, section)
);

-- ===== DailyMed (empty until the archive is supplied) ======================================
CREATE TABLE dailymed_labels (
    set_id TEXT PRIMARY KEY, title TEXT, effective_time TEXT, product_name TEXT,
    active_ingredients_json TEXT, category TEXT
);
CREATE TABLE dailymed_sections (
    section_id INTEGER PRIMARY KEY,
    set_id TEXT NOT NULL REFERENCES dailymed_labels(set_id),
    section TEXT NOT NULL, loinc TEXT, text TEXT NOT NULL, char_len INTEGER NOT NULL,
    UNIQUE (set_id, section)
);

-- ===== cross-source link (exact normalised-name match only) ================================
CREATE TABLE drug_xref (
    ddinter_id TEXT NOT NULL REFERENCES ddinter_drugs(ddinter_id),
    label_id   TEXT NOT NULL REFERENCES openfda_labels(label_id),
    match_type TEXT NOT NULL CHECK (match_type IN ('exact_normalized_name')),
    PRIMARY KEY (ddinter_id, label_id)
);

-- ===== reporting ===========================================================================
CREATE TABLE quality_issues (
    issue_id   INTEGER PRIMARY KEY,
    dataset    TEXT NOT NULL,
    check_name TEXT NOT NULL,
    severity   TEXT NOT NULL CHECK (severity IN ('info','warning','error')),
    affected   INTEGER NOT NULL,
    total      INTEGER NOT NULL,
    detail     TEXT
);
CREATE TABLE feature_coverage (
    feature         TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    available_rows  INTEGER NOT NULL,
    total_rows      INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('available','partial','missing','blocked')),
    source_needed   TEXT,
    blocker         TEXT
);
CREATE TABLE build_manifest (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- ===== convenience views ===================================================================
CREATE VIEW v_ddinter_labeled_pairs AS
    SELECT pair_id, id_lo, id_hi, drug_lo, drug_hi, severity_label
    FROM ddinter_pairs WHERE label_status = 'labeled';
CREATE VIEW v_openfda_ingestible AS
    SELECT * FROM openfda_labels WHERE ingestible = 1;
CREATE VIEW v_openfda_duplicate_section_text AS
    SELECT section, text_sha256, COUNT(*) AS n_labels
    FROM openfda_sections GROUP BY section, text_sha256 HAVING COUNT(*) > 1;
CREATE INDEX ix_pairs_status ON ddinter_pairs(label_status);
CREATE INDEX ix_pairs_lo ON ddinter_pairs(id_lo);
CREATE INDEX ix_pairs_hi ON ddinter_pairs(id_hi);
CREATE INDEX ix_labels_norm ON openfda_labels(name_normalized);
CREATE INDEX ix_drugs_norm ON ddinter_drugs(name_normalized);
CREATE INDEX ix_sections_label ON openfda_sections(label_id);
"""


def create_staging_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def add_source_file(conn: sqlite3.Connection, **kw: Any) -> int:
    cur = conn.execute(
        "INSERT INTO source_files(dataset,path,sha256,size_bytes,row_count,status,note) VALUES (?,?,?,?,?,?,?)",
        (kw["dataset"], kw["path"], kw.get("sha256"), kw.get("size_bytes"), kw.get("row_count"),
         kw["status"], kw.get("note")),
    )
    return int(cur.lastrowid)


def load_ddinter(conn: sqlite3.Connection, res: DDInterResult, file_ids: Dict[str, int]) -> None:
    raw_params = []
    for i, r in enumerate(res.raw_rows, start=1):
        raw_params.append(
            (i, file_ids[r.file], r.line_no, json.dumps(r.fields, ensure_ascii=False),
             r.id_a, r.name_a, r.id_b, r.name_b, r.level, r.row_sha256, r.quarantine_reason)
        )
    conn.executemany(
        "INSERT INTO ddinter_raw(raw_id,file_id,line_no,raw_fields_json,id_a,drug_a,id_b,drug_b,level,"
        "row_sha256,quarantine_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)", raw_params)

    drug_cols = ["ddinter_id", "id_num", "name_original", "name_variants_json", "name_base", "qualifier",
                 "qualifier_kind", "name_normalized", "ingredient_normalized", "n_pairs"]
    conn.executemany(
        f"INSERT INTO ddinter_drugs({','.join(drug_cols)}) VALUES ({','.join('?' * len(drug_cols))})",
        [tuple(d[c] for c in drug_cols) for d in res.drugs])

    pair_cols = ["pair_id", "id_lo", "id_hi", "num_lo", "num_hi", "drug_lo", "drug_hi", "severity_label",
                 "severity_levels_seen", "label_status", "resolution", "n_source_rows", "source_files",
                 "evidence_id", "description"]
    conn.executemany(
        f"INSERT INTO ddinter_pairs({','.join(pair_cols)}) VALUES ({','.join('?' * len(pair_cols))})",
        [tuple(p[c] for c in pair_cols) for p in res.pairs])

    conn.executemany("INSERT INTO ddinter_pair_rows(raw_id,pair_id) VALUES (?,?)",
                     [(idx + 1, pid) for idx, pid in res.pair_row_map])


def load_openfda(conn: sqlite3.Connection, cleaned: Dict[str, Any], file_id: int) -> None:
    label_cols = ["label_id", "source", "brand_name", "generic_name", "name_status", "primary_name",
                  "multi_product", "name_char_len", "name_normalized", "renal_flag", "hepatic_flag",
                  "n_sections", "sections_present", "ingestible", "quarantine_reason", "rxcui"]
    conn.executemany(
        f"INSERT INTO openfda_labels(file_id,{','.join(label_cols)}) VALUES (?,{','.join('?' * len(label_cols))})",
        [(file_id, *[l[c] for c in label_cols]) for l in cleaned["labels"]])
    sec_cols = ["label_id", "section", "text", "text_sha256", "char_len"]
    for batch in chunked(cleaned["sections"], 2000):
        conn.executemany(
            f"INSERT INTO openfda_sections({','.join(sec_cols)}) VALUES ({','.join('?' * len(sec_cols))})",
            [tuple(s[c] for c in sec_cols) for s in batch])


def build_xref(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        INSERT INTO drug_xref(ddinter_id, label_id, match_type)
        SELECT d.ddinter_id, l.label_id, 'exact_normalized_name'
        FROM ddinter_drugs d JOIN openfda_labels l ON l.name_normalized = d.name_normalized
        WHERE l.name_status = 'named' AND d.name_normalized <> ''
        """
    )
    return int(conn.execute("SELECT COUNT(*) FROM drug_xref").fetchone()[0])


def write_issues(conn: sqlite3.Connection, issues: Iterable[Dict[str, Any]]) -> None:
    conn.executemany(
        "INSERT INTO quality_issues(dataset,check_name,severity,affected,total,detail) VALUES (?,?,?,?,?,?)",
        [(i["dataset"], i["check_name"], i["severity"], int(i["affected"]), int(i["total"]), i.get("detail"))
         for i in issues])


def write_features(conn: sqlite3.Connection, features: List[Dict[str, Any]]) -> None:
    conn.executemany(
        "INSERT INTO feature_coverage(feature,category,available_rows,total_rows,status,source_needed,blocker)"
        " VALUES (?,?,?,?,?,?,?)",
        [(f["feature"], f["category"], f["available_rows"], f["total_rows"], f["status"],
          f.get("source_needed"), f.get("blocker")) for f in features])


def write_manifest(conn: sqlite3.Connection, items: Dict[str, Any]) -> None:
    conn.executemany("INSERT INTO build_manifest(key,value) VALUES (?,?)",
                     [(k, v if isinstance(v, str) else json.dumps(v, sort_keys=True)) for k, v in items.items()])
