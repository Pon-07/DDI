"""
openFDA drug-label CSV reading + cleaning.

Findings this module handles (see README "Data issues"):
  * 79.8% of labels in the shipped extract have EMPTY brand_name AND generic_name. The
    production OpenFDAIngestionAdapter rejects them ("Missing generic_name, drug_a, and
    brand_name"), which is why only 4,049 of 20,000 labels reached the database.
    Names are NEVER inferred from label prose; those labels are kept as ``unnamed`` and
    quarantined until the raw openFDA JSON (``openfda.generic_name`` / ``substance_name`` /
    ``rxcui``) is re-extracted.
  * ``renal_related`` / ``hepatic_related`` are the strings "True"/"False" (keyword flags),
    not text. They are converted to booleans and kept as weak flags only.
  * Section text is kept verbatim; exact-duplicate texts (repackaged labels) are flagged by
    hash, not deleted.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

from data_pipeline.names import normalize_name

SECTIONS = [
    "dosage_and_administration",
    "contraindications",
    "drug_interactions",
    "warnings_and_cautions",
    "use_in_specific_populations",
]
FLAG_COLUMNS = ["renal_related", "hepatic_related"]
REQUIRED_COLUMNS = ["id", "brand_name", "generic_name", *SECTIONS, *FLAG_COLUMNS]
NAME_SEPARATOR = " | "
_PLACEHOLDERS = {"nan", "null", "none", "n/a", "na"}


def _present(value: str) -> bool:
    v = (value or "").strip()
    return bool(v) and v.lower() not in _PLACEHOLDERS


def read_openfda_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: missing required columns {missing}")
    return df


def _flag(value: str) -> Tuple[Any, bool]:
    v = (value or "").strip().lower()
    if v == "true":
        return 1, True
    if v == "false":
        return 0, True
    return None, False


def clean_openfda(df: pd.DataFrame, source_name: str) -> Dict[str, Any]:
    labels: List[Dict[str, Any]] = []
    sections: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    seen_ids: Dict[str, int] = {}
    bad_flags = dup_ids = 0

    for row in df.itertuples(index=False):
        rec = row._asdict()
        label_id = (rec["id"] or "").strip()
        if not label_id:
            issues.append({"dataset": "openfda", "check_name": "empty_label_id", "severity": "error",
                           "affected": 1, "total": len(df), "detail": "row skipped from clean tables"})
            continue
        if label_id in seen_ids:
            dup_ids += 1
            continue
        seen_ids[label_id] = 1

        brand = (rec["brand_name"] or "").strip()
        generic = (rec["generic_name"] or "").strip()
        brand = brand if _present(brand) else ""
        generic = generic if _present(generic) else ""
        primary = generic or brand
        named = bool(primary)

        renal, ok_r = _flag(rec["renal_related"])
        hepatic, ok_h = _flag(rec["hepatic_related"])
        if not (ok_r and ok_h):
            bad_flags += 1

        present = []
        for sec in SECTIONS:
            text = rec[sec] or ""
            if _present(text):
                present.append(sec)
                sections.append(
                    {
                        "label_id": label_id,
                        "section": sec,
                        "text": text,  # verbatim
                        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "char_len": len(text),
                    }
                )

        ingestible = named and bool(present)
        labels.append(
            {
                "label_id": label_id,
                "source": source_name,
                "brand_name": brand,
                "generic_name": generic,
                "name_status": "named" if named else "unnamed",
                "primary_name": primary or None,
                "multi_product": 1 if NAME_SEPARATOR in primary else 0,
                "name_char_len": len(primary),
                "name_normalized": normalize_name(primary) if named else None,
                "renal_flag": renal,
                "hepatic_flag": hepatic,
                "n_sections": len(present),
                "sections_present": ";".join(present),
                "ingestible": 1 if ingestible else 0,
                "quarantine_reason": None if ingestible else (
                    "missing_drug_name_in_source_extract" if not named else "no_meaningful_sections"
                ),
                "rxcui": None,
            }
        )

    n = len(df)
    unnamed = sum(1 for l in labels if l["name_status"] == "unnamed")
    issues.append({"dataset": "openfda", "check_name": "labels_without_drug_name", "severity": "error",
                   "affected": unnamed, "total": len(labels),
                   "detail": "brand_name and generic_name both empty in source extract; names not inferred"})
    multi = sum(l["multi_product"] for l in labels)
    issues.append({"dataset": "openfda", "check_name": "multi_product_names", "severity": "warning",
                   "affected": multi, "total": len(labels),
                   "detail": "name field holds several products joined by ' | '; kept verbatim, not split"})
    long_names = sum(1 for l in labels if l["name_char_len"] > 100)
    issues.append({"dataset": "openfda", "check_name": "names_longer_than_100_chars", "severity": "warning",
                   "affected": long_names, "total": len(labels),
                   "detail": "typically combination / homeopathic products; will not match single-drug names"})
    if bad_flags:
        issues.append({"dataset": "openfda", "check_name": "non_boolean_renal_hepatic_flag", "severity": "error",
                       "affected": bad_flags, "total": n, "detail": "expected True/False"})
    if dup_ids:
        issues.append({"dataset": "openfda", "check_name": "duplicate_label_id", "severity": "error",
                       "affected": dup_ids, "total": n, "detail": "later duplicates skipped from clean tables"})

    stats = {
        "source_rows": n,
        "labels": len(labels),
        "named_labels": len(labels) - unnamed,
        "unnamed_labels": unnamed,
        "unnamed_pct": round(100.0 * unnamed / max(len(labels), 1), 1),
        "ingestible_labels": sum(l["ingestible"] for l in labels),
        "sections": len(sections),
        "sections_by_type": {s: sum(1 for x in sections if x["section"] == s) for s in SECTIONS},
        "renal_flag_true": sum(1 for l in labels if l["renal_flag"] == 1),
        "hepatic_flag_true": sum(1 for l in labels if l["hepatic_flag"] == 1),
        "multi_product_names": multi,
        "names_longer_than_100": long_names,
        "duplicate_ids_skipped": dup_ids,
    }
    return {"labels": labels, "sections": sections, "issues": issues, "stats": stats}
