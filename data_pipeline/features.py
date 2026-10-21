"""
ML-feature coverage audit. Reports what exists, what is missing, and what blocks it.
Computed from the staging DB, so the numbers cannot drift from the data.
No feature value is ever invented: unavailable features stay NULL and show up here as missing.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from data_pipeline.common import DDINTER_EXPECTED_CODES

NETWORK_BLOCK = "Build environment has no access to this source (only repo files + PyPI reachable)."


def _scalar(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0] or 0)


def _status(avail: int, total: int, blocked: bool = False) -> str:
    if avail <= 0:
        return "blocked" if blocked else "missing"
    return "available" if avail >= total else "partial"


def compute_feature_coverage(
    conn: sqlite3.Connection, ddinter_codes_present: List[str], openfda_parts_present: int,
    openfda_parts_expected: int = 14, dailymed_rows: int = 0, dailymed_expected: int = 3694,
    raw_openfda_available: bool = False,
) -> List[Dict[str, Any]]:
    drugs = _scalar(conn, "SELECT COUNT(*) FROM ddinter_drugs")
    pairs = _scalar(conn, "SELECT COUNT(*) FROM ddinter_pairs")
    labeled = _scalar(conn, "SELECT COUNT(*) FROM ddinter_pairs WHERE label_status='labeled'")
    labels = _scalar(conn, "SELECT COUNT(*) FROM openfda_labels")
    named = _scalar(conn, "SELECT COUNT(*) FROM openfda_labels WHERE name_status='named'")
    xref_drugs = _scalar(conn, "SELECT COUNT(DISTINCT ddinter_id) FROM drug_xref")
    f: List[Dict[str, Any]] = []

    def add(feature, category, avail, total, source=None, blocker=None, blocked=False, status=None):
        f.append({"feature": feature, "category": category, "available_rows": avail, "total_rows": total,
                  "status": status or _status(avail, total, blocked), "source_needed": source, "blocker": blocker})

    add("severity_label (Major/Moderate/Minor)", "labels", labeled, pairs,
        "DDInter (have it); 'Unknown' pairs need another source to be labelled",
        "Unknown pairs are NOT labels and are excluded; only 'labeled' pairs are trainable")
    add("negative_pairs (non-interacting drug pairs)", "labels", 0, pairs,
        "A curated source of verified NON-interacting pairs (DDInter lists interacting pairs only)",
        "Without negatives only severity classification among known interactions is valid, "
        "not interaction/no-interaction prediction", status="missing")
    add("ddinter_category_files", "coverage", len(ddinter_codes_present), len(DDINTER_EXPECTED_CODES),
        "ddinter_downloads_code_{" + ",".join(DDINTER_EXPECTED_CODES) + "}.csv from DDInter",
        f"Present: {','.join(ddinter_codes_present) or 'none'}. Missing files cannot be downloaded here. {NETWORK_BLOCK}")
    add("interaction_text_or_mechanism", "features", 0, pairs,
        "DDInter interaction descriptions (web/API) or DrugBank interaction text",
        "CSV has no text; legacy JSON 'description' is a copy of the severity word", status="missing")
    add("rxcui (RxNorm)", "identifiers", _scalar(conn, "SELECT COUNT(rxcui) FROM ddinter_drugs"), drugs,
        "RxNorm RRF (free UMLS licence), placed in data_validation/external/ (git-ignored)",
        "Exact normalised-name match to RxNorm ingredient concepts (IN/PIN); ambiguous names skipped; ClinPGx fills a few more. "
        "Unmatched drugs stay NULL. Identifier only: it is not used as a model feature yet.", blocked=True)
    add("atc_codes", "features", _scalar(conn, "SELECT COUNT(atc_codes) FROM ddinter_drugs"), drugs,
        "WHO ATC/DDD index (data_validation/WHO_ATC-DDD*.csv)",
        "Exact normalised-name match to 7-character ATC substance names (WHO file), then ClinPGx ATC ids for the rest; no fuzzy matching; unmatched drugs "
        "stay NULL. DDInter file letters (A,B,D...) are not per-drug ATC codes. WHO licence applies to the raw file.",
        blocked=True)
    add("cyp450_profile (substrate/inhibitor/inducer)", "features",
        _scalar(conn, "SELECT COUNT(cyp_profile) FROM ddinter_drugs"), drugs,
        "FDA 'Table of Substrates, Inhibitors and Inducers' (public) or DrugBank enzymes",
        "Not in repo. " + NETWORK_BLOCK, blocked=True)
    add("drugbank_id", "identifiers", _scalar(conn, "SELECT COUNT(drugbank_id) FROM ddinter_drugs"), drugs,
        "DrugBank (academic licence)", "Not in repo. " + NETWORK_BLOCK, blocked=True)
    add("openfda_labels_with_drug_name", "label_text", named, labels,
        "Re-extract names from RAW openFDA JSON (openfda.generic_name/brand_name/substance_name)",
        "Raw file is a Git-LFS pointer (632 MB not in repo). Names are not inferred from prose.",
        blocked=not raw_openfda_available)
    add("openfda_raw_parts", "coverage", openfda_parts_present, openfda_parts_expected,
        "openFDA drug-label bulk files 1..14", "Only part 1 (20,000 labels) is in the repo. " + NETWORK_BLOCK)
    add("dailymed_labels", "label_text", dailymed_rows, dailymed_expected,
        "dm_spl_monthly_update_aug2026.zip", "Archive not in repo (only validation notes). " + NETWORK_BLOCK,
        blocked=True)
    add("ddinter_drugs_linked_to_openfda_label_text", "linkage", xref_drugs, drugs,
        "Better identifiers (RxCUI) would raise this above exact-name matching",
        "Exact normalised-name match only; no fuzzy matching (would risk wrong links)")
    add("structured_renal_hepatic_dose_rules", "features", 0, 0,
        "Parsed + pharmacist-validated (drug, CrCl/eGFR band, adjustment) table",
        "Label text is narrative; renal_related/hepatic_related are keyword flags only", status="missing")
    return f


def ml_readiness(conn: sqlite3.Connection, features: List[Dict[str, Any]]) -> Dict[str, Any]:
    dist = dict(conn.execute(
        "SELECT severity_label, COUNT(*) FROM ddinter_pairs WHERE label_status='labeled' GROUP BY 1").fetchall())
    total_l = sum(dist.values()) or 1
    by_status = {f["feature"]: f["status"] for f in features}
    missing = [k for k, v in by_status.items() if v in ("missing", "blocked")]
    return {
        "trainable_labeled_pairs": sum(dist.values()),
        "class_distribution": dist,
        "majority_class_share_pct": round(100.0 * max(dist.values(), default=0) / total_l, 1),
        "unusable_unknown_pairs": int(conn.execute(
            "SELECT COUNT(*) FROM ddinter_pairs WHERE label_status='unknown'").fetchone()[0]),
        "conflict_pairs": int(conn.execute(
            "SELECT COUNT(*) FROM ddinter_pairs WHERE label_status='conflict'").fetchone()[0]),
        "missing_or_blocked_features": missing,
        "verdict": (
            "LIMITED READINESS for a feature-based severity model: labels exist, but drug-level features are only "
            "name text and ATC codes for the drugs that matched by exact name; RxCUI, CYP450 and interaction text "
            "are missing. Expect weak generalisation to unseen drugs; evaluate with drug-disjoint splits."
        ),
        "split_warning": "Random pair-level splits leak drug identity; use drug-disjoint (cold-start) splits.",
    }
