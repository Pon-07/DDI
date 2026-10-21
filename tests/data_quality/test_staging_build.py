"""
Data-quality tests against the REAL shipped data. Every expected number is computed
independently from the source files (csv/pandas), not read back from the pipeline.
"""

import csv
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

from data_pipeline.common import PRODUCTION_DB_NAMES, assert_safe_staging_path, sha256_file
from tests.data_quality.conftest import DATA_DIR, REPO_ROOT, needs_real_data

pytestmark = needs_real_data

DD_FILES = sorted(DATA_DIR.glob("ddinter_downloads_code_*.csv"))
OF_FILES = sorted((DATA_DIR / "openFDA").glob("openFDA_validated_labels_part*.csv"))


def _source_ddinter_rows():
    rows = []
    for p in DD_FILES:
        with open(p, encoding="utf-8-sig", newline="") as fh:
            rd = csv.reader(fh)
            next(rd)
            rows += [(p.name, ln, r) for ln, r in ((rd.line_num, r) for r in rd) if r]
    return rows


# ----------------------------------------------------------------------------- DDInter -----
def test_no_raw_ddinter_row_is_lost(staging):
    src = _source_ddinter_rows()
    assert staging.execute("SELECT COUNT(*) FROM ddinter_raw").fetchone()[0] == len(src)


def test_raw_rows_reproduce_source_exactly(staging):
    src = Counter(json.dumps(r) for _, _, r in _source_ddinter_rows())
    got = Counter(r[0] for r in staging.execute("SELECT raw_fields_json FROM ddinter_raw"))
    assert got == src


def test_every_raw_row_maps_to_a_pair_or_is_quarantined(staging):
    n_raw = staging.execute("SELECT COUNT(*) FROM ddinter_raw").fetchone()[0]
    mapped = staging.execute("SELECT COUNT(*) FROM ddinter_pair_rows").fetchone()[0]
    quar = staging.execute("SELECT COUNT(*) FROM ddinter_raw WHERE quarantine_reason IS NOT NULL").fetchone()[0]
    assert mapped + quar == n_raw


def test_severity_counts_match_independent_count(staging):
    # independent: unique unordered pairs and their level straight from the CSV
    levels = {}
    for _, _, r in _source_ddinter_rows():
        key = frozenset((r[0], r[2]))
        levels.setdefault(key, set()).add(r[4].strip().title())
    expect = Counter()
    for lv in levels.values():
        lab = lv - {"Unknown"}
        expect["labeled:" + next(iter(lab)) if len(lab) == 1 else ("unknown" if not lab else "conflict")] += 1
    got = Counter()
    for s, lab, n in staging.execute(
            "SELECT label_status, severity_label, COUNT(*) FROM ddinter_pairs GROUP BY 1, 2"):
        got[f"labeled:{lab}" if s == "labeled" else s] += n
    assert got == expect


def test_unknown_and_conflict_pairs_never_carry_a_label(staging):
    bad = staging.execute("SELECT COUNT(*) FROM ddinter_pairs WHERE label_status<>'labeled' "
                          "AND severity_label IS NOT NULL").fetchone()[0]
    assert bad == 0


def test_no_duplicate_or_self_pairs(staging):
    assert staging.execute("SELECT COUNT(*) FROM (SELECT 1 FROM ddinter_pairs GROUP BY id_lo,id_hi "
                           "HAVING COUNT(*)>1)").fetchone()[0] == 0
    assert staging.execute("SELECT COUNT(*) FROM ddinter_pairs WHERE id_lo=id_hi").fetchone()[0] == 0


def test_descriptions_are_not_severity_echoes(staging):
    assert staging.execute("SELECT COUNT(*) FROM ddinter_pairs WHERE description IS NOT NULL").fetchone()[0] == 0


def test_each_ddinter_id_has_exactly_one_primary_name(staging):
    n_ids = staging.execute("SELECT COUNT(*) FROM ddinter_drugs").fetchone()[0]
    ids = {r[0] for _, _, r in _source_ddinter_rows()} | {r[2] for _, _, r in _source_ddinter_rows()}
    assert n_ids == len(ids)


def test_legacy_json_agrees_with_csv(built):
    chk = built["report"]["after"]["ddinter"]["legacy_json_check"]
    if chk is None:
        pytest.skip("legacy JSON absent")
    assert chk["json_rows_not_in_csv"] == 0 and chk["csv_rows_not_in_json"] == 0


def test_no_different_drugs_merged_except_known_estrone_case(built):
    detail = [i for i in built["report"]["issues"]
              if i["check_name"] == "normalized_name_merges_different_base_drugs"][0]
    merged = json.loads(detail["detail"]) if detail["detail"] else {}
    # documented limitations: the normaliser collapses these pairs onto one name; they are logged, never auto-merged
    assert set(merged) <= {"estrone", "edetate disodium anhydrous", "silver"}, merged


@pytest.mark.skipif(len(DD_FILES) != 8 or sha256_file(DD_FILES[0])[:16] != "5bb75de36056f353",
                    reason="golden numbers only apply to the full DDInter download (categories A,B,D,H,L,P,R,V)")
def test_golden_numbers_for_the_eight_category_files(staging):
    q = lambda s: staging.execute(s).fetchone()[0]
    assert q("SELECT COUNT(*) FROM ddinter_pairs") == 160235
    assert q("SELECT COUNT(*) FROM ddinter_pairs WHERE severity_label='Major'") == 26914
    assert q("SELECT COUNT(*) FROM ddinter_pairs WHERE severity_label='Moderate'") == 96675
    assert q("SELECT COUNT(*) FROM ddinter_pairs WHERE severity_label='Minor'") == 6833
    assert q("SELECT COUNT(*) FROM ddinter_pairs WHERE label_status='unknown'") == 29813
    assert q("SELECT COUNT(*) FROM ddinter_drugs") == 1939
    assert q("SELECT COUNT(*) FROM ddinter_pairs WHERE label_status='conflict'") == 0


# ----------------------------------------------------------------------------- openFDA -----
@pytest.mark.skipif(not OF_FILES, reason="openFDA CSV absent")
class TestOpenFDA:
    @pytest.fixture(scope="class")
    def src(self):
        return pd.concat([pd.read_csv(p, dtype=str, keep_default_na=False) for p in OF_FILES])

    def test_no_label_lost(self, staging, src):
        assert staging.execute("SELECT COUNT(*) FROM openfda_labels").fetchone()[0] == src["id"].nunique()

    def test_unnamed_count_matches_source(self, staging, src):
        unnamed = ((src["generic_name"].str.strip() == "") & (src["brand_name"].str.strip() == "")).sum()
        assert staging.execute("SELECT COUNT(*) FROM openfda_labels WHERE name_status='unnamed'").fetchone()[0] == unnamed

    def test_section_rows_equal_non_empty_cells(self, staging, src):
        secs = ["dosage_and_administration", "contraindications", "drug_interactions",
                "warnings_and_cautions", "use_in_specific_populations"]
        expect = sum((src[c].str.strip() != "").sum() for c in secs)
        assert staging.execute("SELECT COUNT(*) FROM openfda_sections").fetchone()[0] == expect

    def test_section_text_is_verbatim_for_a_random_sample(self, staging, src):
        rng = random.Random(7)
        sample = src.iloc[rng.sample(range(len(src)), 150)]
        for rec in sample.to_dict("records"):
            for sec in ("dosage_and_administration", "contraindications", "drug_interactions"):
                row = staging.execute("SELECT text, text_sha256 FROM openfda_sections WHERE label_id=? AND section=?",
                                      (rec["id"], sec)).fetchone()
                if rec[sec].strip():
                    assert row is not None and row["text"] == rec[sec]
                    assert row["text_sha256"] == hashlib.sha256(rec[sec].encode()).hexdigest()
                else:
                    assert row is None

    def test_ingestible_labels_have_name_and_section(self, staging):
        bad = staging.execute("SELECT COUNT(*) FROM openfda_labels WHERE ingestible=1 AND "
                              "(primary_name IS NULL OR n_sections=0)").fetchone()[0]
        assert bad == 0

    def test_flags_are_boolean_and_match_source(self, staging, src):
        assert staging.execute("SELECT COUNT(*) FROM openfda_labels WHERE renal_flag=1").fetchone()[0] == (src["renal_related"] == "True").sum()
        assert staging.execute("SELECT COUNT(*) FROM openfda_labels WHERE hepatic_flag=1").fetchone()[0] == (src["hepatic_related"] == "True").sum()

    def test_names_are_never_inferred(self, staging, src):
        # every non-null primary_name must literally come from the source generic/brand columns
        names = set(src["generic_name"]) | set(src["brand_name"])
        got = {r[0] for r in staging.execute("SELECT primary_name FROM openfda_labels WHERE primary_name IS NOT NULL")}
        assert got <= names

    def test_legacy_json_ids_match_csv(self, built):
        n = built["report"]["after"]["openfda"].get("legacy_json_ids_matching_csv")
        if n is None:
            pytest.skip("legacy JSON absent")
        assert n == built["report"]["after"]["openfda"]["labels"]


# ---------------------------------------------------------------------- staging DB itself ---
def test_staging_db_integrity_and_single_file(built, staging):
    assert staging.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert staging.execute("PRAGMA foreign_key_check").fetchall() == []
    assert staging.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    assert not list(built["dir"].glob("staging.db-*"))


def test_production_database_untouched(built):
    po = built["report"]["production_untouched"]
    assert po["unchanged"] is True and po["before"] == po["after"]


@pytest.mark.parametrize("name", PRODUCTION_DB_NAMES + ("aegis_rx.db.contaminated-backup",))
def test_staging_refuses_production_paths(name):
    with pytest.raises(RuntimeError):
        assert_safe_staging_path(REPO_ROOT, REPO_ROOT / name)


def test_missing_sources_are_recorded_not_hidden(staging):
    rows = staging.execute("SELECT dataset, path, status FROM source_files WHERE status<>'ingested'").fetchall()
    kinds = {(r["dataset"], r["status"]) for r in rows}
    assert ("dailymed", "missing") in kinds
    assert ("ddinter", "missing") in kinds or len(DD_FILES) == 8


def test_feature_slots_are_empty_never_fabricated(staging):
    for col in ("cyp_profile", "drugbank_id"):
        assert staging.execute(f"SELECT COUNT({col}) FROM ddinter_drugs").fetchone()[0] == 0
    # RxCUI only from RxNorm / ClinPGx files in data_validation/external/, by exact name, one source row per value
    n_rx = staging.execute("SELECT COUNT(rxcui) FROM ddinter_drugs").fetchone()[0]
    if (DATA_DIR / "external").exists() and any((DATA_DIR / "external").glob("RxNorm*.zip")):
        total = staging.execute("SELECT COUNT(*) FROM ddinter_drugs").fetchone()[0]
        assert 0 < n_rx < total
        assert staging.execute("SELECT COUNT(*) FROM (SELECT ddinter_id FROM drug_rxcui GROUP BY 1 HAVING COUNT(*)>1)").fetchone()[0] == 0
    else:
        assert n_rx == 0
    # ATC is filled only from a WHO file in data_validation/, by exact normalised name, and only when that file exists
    n_atc = staging.execute("SELECT COUNT(atc_codes) FROM ddinter_drugs").fetchone()[0]
    if list(DATA_DIR.glob("WHO_ATC-DDD*.csv")):
        assert 0 < n_atc < staging.execute("SELECT COUNT(*) FROM ddinter_drugs").fetchone()[0]
        assert staging.execute("SELECT COUNT(*) FROM drug_atc WHERE match_type NOT IN "
                               "('exact_normalized_name','clinpgx_exact_name')").fetchone()[0] == 0
    else:
        assert n_atc == 0
    assert staging.execute("SELECT COUNT(rxcui) FROM openfda_labels").fetchone()[0] == 0


def test_feature_coverage_reports_missing_ml_features(staging):
    st = {r["feature"]: r["status"] for r in staging.execute("SELECT feature, status FROM feature_coverage")}
    for f in ("cyp450_profile (substrate/inhibitor/inducer)", "negative_pairs (non-interacting drug pairs)"):
        assert st[f] in ("missing", "blocked")
    assert st["rxcui (RxNorm)"] in ("partial", "missing", "blocked")
    has_who = bool(list(DATA_DIR.glob("WHO_ATC-DDD*.csv")))
    assert st["atc_codes"] == ("partial" if has_who else st["atc_codes"])
    assert st["atc_codes"] in ("partial", "missing", "blocked")


def test_ml_readiness_does_not_overclaim(built):
    r = built["report"]["ml_readiness"]
    # never a green light: either "NOT READY" (no features) or "LIMITED READINESS" (name text + ATC only)
    assert r["verdict"].startswith(("NOT READY", "LIMITED READINESS"))
    for word in ("ready for clinical", "validated", "accurate"):
        assert word not in r["verdict"].lower().replace("not been clinically validated", "")
    assert r["trainable_labeled_pairs"] == sum(r["class_distribution"].values())


def test_clean_csv_exports_match_staging(built, staging):
    n = sum(1 for _ in open(built["clean"] / "ddinter_pairs_clean.csv", encoding="utf-8")) - 1
    assert n == staging.execute("SELECT COUNT(*) FROM ddinter_pairs").fetchone()[0]


def test_build_is_reproducible(built, tmp_path):
    from data_pipeline.build import run_build

    run_build(REPO_ROOT, tmp_path / "again.db", tmp_path / "clean", None, include_legacy=False)
    import sqlite3

    def digest(db, table, order):
        c = sqlite3.connect(db)
        h = hashlib.sha256()
        for row in c.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            h.update(repr(row).encode())
        return h.hexdigest()

    for table, order in (("ddinter_raw", "raw_id"), ("ddinter_drugs", "ddinter_id"), ("ddinter_pairs", "pair_id"),
                         ("openfda_labels", "label_id"), ("openfda_sections", "label_id, section"),
                         ("drug_xref", "ddinter_id, label_id")):
        assert digest(built["db"], table, order) == digest(tmp_path / "again.db", table, order), table
