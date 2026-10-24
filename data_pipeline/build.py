"""
End-to-end, reproducible dataset build.

    python -m data_pipeline.build                      # from the repository root
    python -m data_pipeline.build --staging-db staging/aegis_staging.db --no-legacy

Outputs
  staging/aegis_staging.db                     separate staging DB (git-ignored; never the production DB)
  datasets/clean/*.csv                         clean, ML-friendly exports (small; committed)
  reports/data_quality/data_quality_report.*   JSON + Markdown before/after report

The production database files (aegis_rx.db*) are fingerprinted before and after the run; the
build aborts with an error if a single byte changed.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from data_pipeline import PIPELINE_VERSION
from data_pipeline import atc, crosswalk
from data_pipeline import dailymed as dm
from data_pipeline import ddinter as ddi
from data_pipeline import features as feat
from data_pipeline import openfda as ofd
from data_pipeline import staging as stg
from data_pipeline.common import (
    DDINTER_EXPECTED_CODES,
    LEGACY_BACKUP_NAME,
    assert_safe_staging_path,
    ddinter_code,
    discover_ddinter_files,
    is_lfs_pointer,
    open_copy_readonly,
    production_fingerprints,
    sha256_file,
)
from data_pipeline.names import split_qualifier

DATA_DIR = "data_validation"
OPENFDA_PARTS_EXPECTED = 14


# ----------------------------------------------------------------------------------------
# "BEFORE": audit of the databases as shipped (read from COPIES, never the originals)
# ----------------------------------------------------------------------------------------
def _counts(conn: sqlite3.Connection, tables: List[str]) -> Dict[str, Optional[int]]:
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {t: (conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] if t in existing else None)
            for t in tables}


KNOWLEDGE_TABLES = ["drugs", "interaction_evidence", "label_evidence", "safety_rules", "rule_versions"]
CLINICAL_TABLES = ["patients", "medications", "labs", "orders", "events", "findings", "audit_ledger"]


def audit_legacy_databases(repo_root: Path, csv_pairs: Optional[set]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for label, name in (("production_db", "aegis_rx.db"), ("legacy_backup", LEGACY_BACKUP_NAME)):
        opened = open_copy_readonly(repo_root / name)
        if opened is None:
            out[label] = {"present": False}
            continue
        conn, tmp = opened
        info: Dict[str, Any] = {"present": True}
        try:
            info["integrity_check"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
            info["knowledge_rows"] = _counts(conn, KNOWLEDGE_TABLES)
            info["clinical_rows"] = _counts(conn, CLINICAL_TABLES)
            if info["knowledge_rows"]["interaction_evidence"]:
                rows = conn.execute("SELECT drug_a, drug_b, severity, description FROM interaction_evidence").fetchall()
                info["interaction_description_equals_severity"] = sum(1 for r in rows if r[3] == r[2])
                if csv_pairs is not None:
                    got = {(r[0], r[1], r[2]) for r in rows}
                    info["interactions_matching_source_csv"] = len(got & csv_pairs)
                    info["interactions_not_in_source_csv"] = len(got - csv_pairs)
                    info["source_csv_pairs_missing_from_db"] = len(csv_pairs - got)
            if info["knowledge_rows"]["label_evidence"]:
                info["label_evidence_duplicate_text_groups"] = conn.execute(
                    "SELECT COUNT(*) FROM (SELECT 1 FROM label_evidence GROUP BY drug_id, section, text "
                    "HAVING COUNT(*) > 1)").fetchone()[0]
                info["label_evidence_sections"] = dict(conn.execute(
                    "SELECT section, COUNT(*) FROM label_evidence GROUP BY section").fetchall())
                info["drugs_by_source"] = dict(conn.execute(
                    "SELECT source, COUNT(*) FROM drugs GROUP BY source").fetchall())
                info["drugs_with_rxnorm"] = conn.execute(
                    "SELECT COUNT(*) FROM drugs WHERE rxnorm_code IS NOT NULL").fetchone()[0]
            if info["clinical_rows"]["patients"]:
                info["test_fixture_patients"] = conn.execute(
                    "SELECT COUNT(*) FROM patients WHERE patient_identifier LIKE 'PAT-%'").fetchone()[0]
        finally:
            conn.close()
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        out[label] = info
    return out


# ----------------------------------------------------------------------------------------
def _write_csv(path: Path, rows: List[Dict[str, Any]], columns: List[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow(["" if r.get(c) is None else r.get(c) for c in columns])
    return len(rows)


def _verify_ddinter_json(path: Path, raw_rows: List[ddi.RawRow]) -> Dict[str, Any]:
    """Legacy ddinter_normalized.json vs the CSV source of truth (read-only comparison)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    csv_set = Counter((r.name_a, r.name_b, r.level) for r in raw_rows if not r.quarantine_reason)
    js_set = Counter((d.get("drug_a"), d.get("drug_b"), d.get("severity")) for d in data)
    return {
        "json_rows": len(data),
        "json_rows_matching_csv": sum((csv_set & js_set).values()),
        "json_rows_not_in_csv": sum((js_set - csv_set).values()),
        "csv_rows_not_in_json": sum((csv_set - js_set).values()),
        "description_equals_severity": sum(1 for d in data if d.get("interaction_description") == d.get("severity")),
    }


def run_build(
    repo_root: Path,
    staging_db: Path,
    clean_dir: Path,
    reports_dir: Optional[Path],
    include_legacy: bool = True,
) -> Dict[str, Any]:
    repo_root = repo_root.resolve()
    staging_db = staging_db.resolve()
    assert_safe_staging_path(repo_root, staging_db)
    data_dir = repo_root / DATA_DIR
    fp_before = production_fingerprints(repo_root)

    conn = stg.create_staging_db(staging_db)
    report: Dict[str, Any] = {"pipeline_version": PIPELINE_VERSION, "inputs": []}
    issues: List[Dict[str, Any]] = []
    file_ids: Dict[str, int] = {}
    atc_source: Dict[str, str] = {}   # ddinter_id -> origin of its ATC codes (written only to the restricted export)

    # ===== DDInter ===========================================================================
    dd_files = discover_ddinter_files(data_dir)
    if not dd_files:
        raise FileNotFoundError(f"No ddinter_downloads_code_*.csv found in {data_dir}")
    raw_rows: List[ddi.RawRow] = []
    for p in dd_files:
        rows = ddi.read_ddinter_csv(p)
        rel = f"{DATA_DIR}/{p.name}"
        fid = stg.add_source_file(conn, dataset="ddinter", path=rel, sha256=sha256_file(p),
                                  size_bytes=p.stat().st_size, row_count=len(rows), status="ingested")
        file_ids[p.name] = fid
        report["inputs"].append({"path": rel, "sha256": sha256_file(p), "rows": len(rows)})
        raw_rows.extend(rows)
    present_codes = sorted(c for c in (ddinter_code(p) for p in dd_files) if c)
    for code in DDINTER_EXPECTED_CODES:
        if code not in present_codes:
            stg.add_source_file(conn, dataset="ddinter", path=f"{DATA_DIR}/ddinter_downloads_code_{code}.csv",
                                status="missing", note="Not in repository; not downloadable from the build "
                                "environment. Place the file there and re-run the build.")
    dd = ddi.clean_ddinter(raw_rows)
    stg.load_ddinter(conn, dd, file_ids)
    issues.extend(dd.issues)

    # ===== WHO ATC (optional; exact normalised-name matching only) =============================
    atc_files = sorted(data_dir.glob("WHO_ATC-DDD*.csv"))
    atc_stats = None
    if atc_files:
        atc_path = atc_files[-1]
        atc_rows = atc.read_atc(atc_path)
        stg.add_source_file(conn, dataset="who_atc", path=f"{DATA_DIR}/{atc_path.name}", sha256=sha256_file(atc_path),
                            size_bytes=atc_path.stat().st_size, row_count=len(atc_rows), status="ingested")
        report["inputs"].append({"path": f"{DATA_DIR}/{atc_path.name}", "sha256": sha256_file(atc_path), "rows": len(atc_rows)})
        conn.executemany("INSERT INTO atc_codes(atc_code,atc_name,ddd,uom,adm_r,note) VALUES (?,?,?,?,?,?)",
                         [(r["atc_code"], r["atc_name"], r["ddd"], r["uom"], r["adm_r"], r["note"]) for r in atc_rows])
        matched, atc_stats = atc.match_drugs(dd.drugs, atc_rows)
        conn.executemany("INSERT INTO drug_atc VALUES (?,?, 'exact_normalized_name')",
                         [(i, c) for i, codes in matched.items() for c in codes])
        conn.executemany("UPDATE ddinter_drugs SET atc_codes=? WHERE ddinter_id=?",
                         [(";".join(codes), i) for i, codes in matched.items()])
        for d in dd.drugs:  # keep the in-memory rows consistent with the staging table
            if d["ddinter_id"] in matched:
                d["atc_codes"] = ";".join(matched[d["ddinter_id"]])
                atc_source[d["ddinter_id"]] = "who_atc_exact_name"
        report["atc"] = atc_stats

    # ===== RxNorm + ClinPGx cross-references (optional, not committed: data_validation/external/) =========
    ext_dir = data_dir / "external"
    rx_files = sorted(ext_dir.glob("RxNorm*.zip")) + sorted(ext_dir.glob("RXNCONSO.RRF"))
    cp_files = sorted(ext_dir.glob("drugs.zip")) + sorted(ext_dir.glob("drugs.tsv"))
    xw_stats: Dict[str, Any] = {}
    rx_by_id: Dict[str, str] = {}
    if rx_files:
        rx_path = rx_files[0]
        stg.add_source_file(conn, dataset="rxnorm", path=f"{DATA_DIR}/external/{rx_path.name}", sha256=sha256_file(rx_path),
                            size_bytes=rx_path.stat().st_size, row_count=None, status="ingested")
        rx_matched, rx_stats = crosswalk.match_rxnorm(dd.drugs, crosswalk.read_rxnorm_ingredients(rx_path))
        conn.executemany("INSERT INTO drug_rxcui VALUES (?,?,?,'rxnorm_exact_name')",
                         [(i, rc, tty) for i, (rc, tty) in rx_matched.items()])
        rx_by_id.update({i: rc for i, (rc, _t) in rx_matched.items()})
        xw_stats.update(rx_stats)
    if cp_files:
        cp_path = cp_files[0]
        stg.add_source_file(conn, dataset="clinpgx", path=f"{DATA_DIR}/external/{cp_path.name}", sha256=sha256_file(cp_path),
                            size_bytes=cp_path.stat().st_size, row_count=None, status="ingested")
        have_atc = {d["ddinter_id"] for d in dd.drugs if d.get("atc_codes")}
        cp_rx, cp_atc, cp_stats = crosswalk.clinpgx_topup(dd.drugs, crosswalk.read_clinpgx_drugs(cp_path),
                                                          set(rx_by_id), have_atc)
        conn.executemany("INSERT INTO drug_rxcui VALUES (?,?,NULL,'clinpgx_exact_name')", list(cp_rx.items()))
        conn.executemany("INSERT INTO drug_atc VALUES (?,?, 'clinpgx_exact_name')",
                         [(i, c) for i, codes in cp_atc.items() for c in codes])
        conn.executemany("UPDATE ddinter_drugs SET atc_codes=? WHERE ddinter_id=?",
                         [(";".join(codes), i) for i, codes in cp_atc.items()])
        rx_by_id.update(cp_rx)
        for d in dd.drugs:
            if d["ddinter_id"] in cp_atc:
                d["atc_codes"] = ";".join(cp_atc[d["ddinter_id"]])
                atc_source[d["ddinter_id"]] = "clinpgx_atc_id_exact_name"
        xw_stats.update(cp_stats)
    if rx_by_id:
        conn.executemany("UPDATE ddinter_drugs SET rxcui=? WHERE ddinter_id=?", [(rc, i) for i, rc in rx_by_id.items()])
        for d in dd.drugs:
            if d["ddinter_id"] in rx_by_id:
                d["rxcui"] = rx_by_id[d["ddinter_id"]]
    report["crosswalk"] = xw_stats

    legacy_json = data_dir / "ddinter_normalized.json"
    json_check = None
    if legacy_json.exists():
        # the legacy JSON was exported from category A only; compare like with like
        json_check = _verify_ddinter_json(legacy_json, [r for r in raw_rows if r.file == "ddinter_downloads_code_A.csv"])
        stg.add_source_file(conn, dataset="ddinter_legacy_json", path=f"{DATA_DIR}/ddinter_normalized.json",
                            sha256=sha256_file(legacy_json), size_bytes=legacy_json.stat().st_size,
                            row_count=json_check["json_rows"], status="ingested",
                            note="verification only; CSV is the source of truth")
        issues.append({"dataset": "ddinter", "check_name": "legacy_json_description_is_severity_echo",
                       "severity": "warning", "affected": json_check["description_equals_severity"],
                       "total": json_check["json_rows"],
                       "detail": "interaction_description == severity in legacy JSON; not carried into clean tables"})
        if json_check["json_rows_not_in_csv"] or json_check["csv_rows_not_in_json"]:
            issues.append({"dataset": "ddinter", "check_name": "legacy_json_differs_from_csv", "severity": "error",
                           "affected": json_check["json_rows_not_in_csv"] + json_check["csv_rows_not_in_json"],
                           "total": json_check["json_rows"], "detail": "legacy JSON and CSV disagree"})

    # name normalisation sanity: distinct base drugs that collapse to one normalised name
    groups: Dict[str, set] = {}
    for d in dd.drugs:
        groups.setdefault(d["name_normalized"], set()).add(split_qualifier(d["name_original"])[0].lower())
    collisions = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
    issues.append({"dataset": "ddinter", "check_name": "normalized_name_merges_different_base_drugs",
                   "severity": "error" if collisions else "info", "affected": len(collisions),
                   "total": len(groups), "detail": json.dumps(dict(list(collisions.items())[:10]))})
    qual_merges = sum(1 for k, v in Counter(d["name_normalized"] for d in dd.drugs).items() if v > 1)
    issues.append({"dataset": "ddinter", "check_name": "route_or_salt_variants_share_normalized_name",
                   "severity": "info", "affected": qual_merges, "total": len(groups),
                   "detail": "e.g. 'Atropine' and 'Atropine (ophthalmic)'. Drug identity in staging is the "
                             "DDInter ID, so these stay separate entities; join on ingredient_normalized if needed"})

    # ===== openFDA ===========================================================================
    of_csvs = sorted((data_dir / "openFDA").glob("openFDA_validated_labels_part*.csv"))
    of_stats: Dict[str, Any] = {}
    of_labels: List[Dict[str, Any]] = []
    parts_present = 0
    for p in of_csvs:
        df = ofd.read_openfda_csv(p)
        rel = f"{DATA_DIR}/openFDA/{p.name}"
        fid = stg.add_source_file(conn, dataset="openfda", path=rel, sha256=sha256_file(p),
                                  size_bytes=p.stat().st_size, row_count=len(df), status="ingested")
        report["inputs"].append({"path": rel, "sha256": sha256_file(p), "rows": len(df)})
        cleaned = ofd.clean_openfda(df, source_name="openfda")
        stg.load_openfda(conn, cleaned, fid)
        issues.extend(cleaned["issues"])
        of_stats = cleaned["stats"]
        of_labels = cleaned["labels"]
        parts_present += 1
    raw_openfda_ok = False
    for p in sorted((data_dir / "raw").glob("drug-label-*.json")) if (data_dir / "raw").exists() else []:
        if is_lfs_pointer(p):
            stg.add_source_file(conn, dataset="openfda_raw", path=f"{DATA_DIR}/raw/{p.name}", sha256=sha256_file(p),
                                size_bytes=p.stat().st_size, status="lfs_pointer",
                                note="Git-LFS pointer only (real file ~632 MB not in repo; LFS objects are not "
                                     "served to anonymous clones). Needed to recover drug names, RxCUI, substance names.")
            issues.append({"dataset": "openfda", "check_name": "raw_file_is_git_lfs_pointer", "severity": "error",
                           "affected": 1, "total": 1, "detail": p.name})
        else:
            raw_openfda_ok = True
            stg.add_source_file(conn, dataset="openfda_raw", path=f"{DATA_DIR}/raw/{p.name}", sha256=sha256_file(p),
                                size_bytes=p.stat().st_size, status="blocked",
                                note="Real raw file present, but raw-JSON re-extraction is not implemented yet.")
    for n in range(parts_present + 1, OPENFDA_PARTS_EXPECTED + 1):
        stg.add_source_file(conn, dataset="openfda", path=f"{DATA_DIR}/openFDA/openFDA_validated_labels_part{n}.csv",
                            status="missing", note="Not in repository / not downloadable from the build environment.")

    # legacy normalised JSON vs CSV consistency
    nparts = sorted((data_dir / "openFDA" / "normalized_parts").glob("openFDA_normalized_part*.json"))
    if nparts and of_labels:
        csv_names = {l["label_id"]: (l["brand_name"], l["generic_name"]) for l in of_labels}
        json_ids, name_mismatch = set(), 0
        for p in nparts:
            for rec in json.loads(p.read_text(encoding="utf-8")):
                ev = rec.get("evidence") or {}
                json_ids.add(ev.get("id"))
                got = ((ev.get("brand_name") or "").strip(), (ev.get("generic_name") or "").strip())
                if ev.get("id") in csv_names and csv_names[ev["id"]] != got:
                    name_mismatch += 1
        of_stats["legacy_json_ids_matching_csv"] = len(json_ids & set(csv_names))
        of_stats["legacy_json_name_mismatches"] = name_mismatch
        stg.add_source_file(conn, dataset="openfda_legacy_json", path=f"{DATA_DIR}/openFDA/normalized_parts/",
                            row_count=len(json_ids), status="ingested", note="verification only")

    # ===== DailyMed ==========================================================================
    dm_zips = sorted((data_dir / "dailymed").glob("*.zip")) if (data_dir / "dailymed").exists() else []
    dm_rows = 0
    if dm_zips:
        seen = set()
        for z in dm_zips:
            fid = stg.add_source_file(conn, dataset="dailymed", path=f"{DATA_DIR}/dailymed/{z.name}",
                                      sha256=sha256_file(z), size_bytes=z.stat().st_size, status="ingested")
            for rec in dm.iter_dailymed_zip(z):
                if not rec["set_id"] or rec["set_id"] in seen:
                    continue
                seen.add(rec["set_id"])
                conn.execute("INSERT INTO dailymed_labels VALUES (?,?,?,?,?,?)",
                             (rec["set_id"], rec["title"], rec["effective_time"], rec["product_name"],
                              json.dumps(rec["active_ingredients"]), rec["category"]))
                for s in {x["section"]: x for x in rec["sections"]}.values():
                    conn.execute("INSERT INTO dailymed_sections(set_id,section,loinc,text,char_len) VALUES (?,?,?,?,?)",
                                 (rec["set_id"], s["section"], s["loinc"], s["text"], s["char_len"]))
            dm_rows = len(seen)
    else:
        stg.add_source_file(conn, dataset="dailymed", path=f"{DATA_DIR}/dailymed/dm_spl_monthly_update_aug2026.zip",
                            status="missing", note="Only validation notes exist in the repo (DailyMed_validation_notes.txt). "
                            "3,694 label packages reported; archive not present, so nothing could be ingested.")
        issues.append({"dataset": "dailymed", "check_name": "source_archive_missing", "severity": "error",
                       "affected": 3694, "total": 3694,
                       "detail": "no DailyMed data ingested; parser is ready and tested (data_pipeline/dailymed.py)"})

    # ===== linkage, features, reports ========================================================
    xref = stg.build_xref(conn)
    stg.write_issues(conn, issues)
    feats = feat.compute_feature_coverage(conn, present_codes, parts_present, OPENFDA_PARTS_EXPECTED,
                                          dailymed_rows=dm_rows, raw_openfda_available=raw_openfda_ok)
    stg.write_features(conn, feats)
    readiness = feat.ml_readiness(conn, feats)

    csv_pairs = {(r.name_a, r.name_b, r.level) for r in raw_rows if not r.quarantine_reason}
    before = audit_legacy_databases(repo_root, csv_pairs) if include_legacy else {}

    # ----- integrity assertions (fail loudly rather than ship silently-wrong data) ----------
    cur = conn.cursor()
    n_raw = cur.execute("SELECT COUNT(*) FROM ddinter_raw").fetchone()[0]
    assert n_raw == sum(len(ddi.read_ddinter_csv(p)) for p in dd_files), "raw DDInter rows lost"
    n_mapped = cur.execute("SELECT COUNT(*) FROM ddinter_pair_rows").fetchone()[0]
    n_quar = cur.execute("SELECT COUNT(*) FROM ddinter_raw WHERE quarantine_reason IS NOT NULL").fetchone()[0]
    assert n_mapped + n_quar == n_raw, "every raw row must map to a pair or be quarantined"
    assert cur.execute("PRAGMA foreign_key_check").fetchall() == [], "foreign key violations"
    assert cur.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    n_of = cur.execute("SELECT COUNT(*) FROM openfda_labels").fetchone()[0]
    assert n_of + of_stats.get("duplicate_ids_skipped", 0) == of_stats.get("source_rows", 0), "openFDA labels lost"

    fp_after = production_fingerprints(repo_root)
    if fp_before != fp_after:
        raise RuntimeError("Production database files changed during the staging build! Aborting.")

    tables = ["source_files", "ddinter_raw", "ddinter_drugs", "ddinter_pairs", "ddinter_pair_rows",
              "openfda_labels", "openfda_sections", "dailymed_labels", "dailymed_sections", "drug_xref", "atc_codes", "drug_atc", "drug_rxcui",
              "quality_issues", "feature_coverage"]
    table_counts = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    # ----- clean CSV exports -------------------------------------------------------------------
    pair_cols = ["pair_id", "id_lo", "id_hi", "drug_lo", "drug_hi", "severity_label", "label_status",
                 "severity_levels_seen", "resolution", "n_source_rows", "source_files", "evidence_id"]
    drug_cols = ["ddinter_id", "name_original", "name_base", "qualifier", "qualifier_kind", "name_normalized",
                 "ingredient_normalized", "n_pairs", "rxcui", "atc_codes", "cyp_profile", "drugbank_id"]
    label_cols = ["label_id", "source", "brand_name", "generic_name", "name_status", "primary_name",
                  "multi_product", "name_normalized", "renal_flag", "hepatic_flag", "n_sections",
                  "sections_present", "ingestible", "quarantine_reason", "rxcui"]
    # WHO ATC (and ATC ids taken from ClinPGx) are WHO-derived and their redistribution terms are unresolved, so they are NOT
    # written to the committed clean CSV. They go to datasets/restricted/ (git-ignored); the model loads them only if present.
    restricted_dir = clean_dir.parent / "restricted"
    restricted_rows = [{"ddinter_id": d["ddinter_id"], "atc_codes": d["atc_codes"], "atc_source": atc_source.get(d["ddinter_id"], "")}
                       for d in dd.drugs if d.get("atc_codes")]
    public_drugs = [{**d, "atc_codes": ""} for d in dd.drugs]
    restricted_exports: Dict[str, Any] = {}
    if restricted_rows:
        restricted_exports["drug_atc_restricted.csv"] = _write_csv(restricted_dir / "drug_atc_restricted.csv", restricted_rows,
                                                                    ["ddinter_id", "atc_codes", "atc_source"])
    exports = {
        "ddinter_pairs_clean.csv": _write_csv(clean_dir / "ddinter_pairs_clean.csv", dd.pairs, pair_cols),
        "ddinter_drugs_clean.csv": _write_csv(clean_dir / "ddinter_drugs_clean.csv", public_drugs, drug_cols),
        "openfda_labels_clean.csv": _write_csv(clean_dir / "openfda_labels_clean.csv", of_labels, label_cols),
        "quality_issues.csv": _write_csv(clean_dir / "quality_issues.csv", issues,
                                          ["dataset", "check_name", "severity", "affected", "total", "detail"]),
        "feature_coverage.csv": _write_csv(clean_dir / "feature_coverage.csv", feats,
                                            ["feature", "category", "available_rows", "total_rows", "status",
                                             "source_needed", "blocker"]),
    }

    report.update(
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "before": before,
            "after": {
                "ddinter": dd.stats | {"files_present": present_codes,
                                        "files_expected": list(DDINTER_EXPECTED_CODES),
                                        "legacy_json_check": json_check,
                                        "base_name_collisions_after_normalization": len(collisions)},
                "openfda": of_stats | {"raw_parts_present": parts_present, "raw_parts_expected": OPENFDA_PARTS_EXPECTED},
                "dailymed": {"labels_ingested": dm_rows, "status": "ingested" if dm_rows else "missing"},
                "cross_source_xref_rows": xref,
                "staging_table_rows": table_counts,
                "clean_csv_exports": exports,
                "restricted_exports_not_committed": restricted_exports,
            },
            "feature_coverage": feats,
            "ml_readiness": readiness,
            "production_untouched": {"before": fp_before, "after": fp_after, "unchanged": fp_before == fp_after},
            "issues": issues,
        }
    )
    stg.write_manifest(conn, {
        "pipeline_version": PIPELINE_VERSION,
        "inputs": report["inputs"],
        "table_rows": table_counts,
        "production_untouched": report["production_untouched"]["unchanged"],
    })
    conn.commit()
    conn.execute("VACUUM")
    conn.close()

    if reports_dir is not None:
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "data_quality_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        from data_pipeline.report import render_markdown
        (reports_dir / "data_quality_report.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build the AEGIS Rx staging DB and clean datasets (never touches production).")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--staging-db", default="staging/aegis_staging.db")
    ap.add_argument("--clean-dir", default="datasets/clean")
    ap.add_argument("--reports-dir", default="reports/data_quality")
    ap.add_argument("--no-legacy", action="store_true", help="skip the audit of aegis_rx.db / backup copies")
    a = ap.parse_args(argv)
    root = Path(a.repo_root).resolve()
    report = run_build(root, root / a.staging_db, root / a.clean_dir, root / a.reports_dir,
                       include_legacy=not a.no_legacy)
    after = report["after"]
    print(json.dumps({"ddinter": {k: after["ddinter"][k] for k in
                                   ("raw_rows", "pairs", "drugs", "pairs_by_status", "pairs_by_label")},
                      "openfda": {k: after["openfda"][k] for k in
                                  ("source_rows", "named_labels", "unnamed_labels", "ingestible_labels", "sections")},
                      "production_untouched": report["production_untouched"]["unchanged"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
