"""
DDInter CSV reading + cleaning.

Raw rows are kept verbatim; the cleaning step derives one record per UNORDERED drug pair.

Cleaning rules (all documented in README):
  * A pair is identified by the two DDInter IDs, order-insensitive. ``(A,B)`` and ``(B,A)``
    are the same interaction (DDInter repeats pairs across its per-ATC-category files).
  * ``Unknown`` is the source's own "no severity" value. It is kept, but is NOT a training
    label (``severity_label`` is NULL, ``label_status='unknown'``).
  * If duplicate rows of a pair disagree on severity, the pair is flagged
    ``label_status='conflict'`` and gets NO label. Nothing is resolved by guessing
    (no "take the max"). A labelled level beats ``Unknown`` for the same pair, because
    ``Unknown`` carries no information.
  * Rows with malformed IDs, wrong column count, empty names, self-pairs or an
    unrecognised level are quarantined with a reason (never deleted from the raw table).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from data_pipeline.names import (
    clean_display_name,
    normalize_name,
    qualifier_kind,
    split_qualifier,
)

EXPECTED_HEADER = ["DDInterID_A", "Drug_A", "DDInterID_B", "Drug_B", "Level"]
CANONICAL_LEVELS = {"major": "Major", "moderate": "Moderate", "minor": "Minor", "unknown": "Unknown"}
LABELLED_LEVELS = ("Major", "Moderate", "Minor")
_ID_RE = re.compile(r"^DDInter(\d+)$")


@dataclass
class RawRow:
    file: str
    line_no: int
    fields: List[str]
    id_a: Optional[str] = None
    name_a: Optional[str] = None
    id_b: Optional[str] = None
    name_b: Optional[str] = None
    level: Optional[str] = None
    row_sha256: str = ""
    quarantine_reason: Optional[str] = None


@dataclass
class DDInterResult:
    raw_rows: List[RawRow] = field(default_factory=list)
    pairs: List[Dict[str, Any]] = field(default_factory=list)
    drugs: List[Dict[str, Any]] = field(default_factory=list)
    pair_row_map: List[Tuple[int, int]] = field(default_factory=list)  # (raw_index, pair_id)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


def _row_sha(fields: List[str]) -> str:
    return hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()


def read_ddinter_csv(path: Path, display_name: Optional[str] = None) -> List[RawRow]:
    """Read one DDInter CSV verbatim. Raises ValueError on an unexpected header."""
    name = display_name or path.name
    text = path.read_bytes().decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise ValueError(f"{name}: empty file")
    if header != EXPECTED_HEADER:
        raise ValueError(f"{name}: unexpected header {header!r}, expected {EXPECTED_HEADER!r}")

    rows: List[RawRow] = []
    for fields in reader:
        line_no = reader.line_num
        if not fields:  # fully blank line: nothing to preserve
            continue
        row = RawRow(file=name, line_no=line_no, fields=list(fields), row_sha256=_row_sha(fields))
        if len(fields) == 5:
            row.id_a, row.name_a, row.id_b, row.name_b, row.level = fields
        else:
            row.quarantine_reason = f"wrong_column_count_{len(fields)}"
        rows.append(row)
    return rows


def _id_num(ddid: Optional[str]) -> Optional[int]:
    m = _ID_RE.match((ddid or "").strip())
    return int(m.group(1)) if m else None


def _validate_row(row: RawRow) -> None:
    """Set row.quarantine_reason for unusable rows (leaves valid rows untouched)."""
    if row.quarantine_reason:
        return
    ida, idb = (row.id_a or "").strip(), (row.id_b or "").strip()
    if _id_num(ida) is None or _id_num(idb) is None:
        row.quarantine_reason = "malformed_ddinter_id"
    elif not clean_display_name(row.name_a or "") or not clean_display_name(row.name_b or ""):
        row.quarantine_reason = "empty_drug_name"
    elif ida == idb:
        row.quarantine_reason = "self_pair"
    elif (row.level or "").strip().lower() not in CANONICAL_LEVELS:
        row.quarantine_reason = "invalid_level"


def clean_ddinter(raw_rows: List[RawRow]) -> DDInterResult:
    res = DDInterResult(raw_rows=raw_rows)
    for r in raw_rows:
        _validate_row(r)

    # --- group valid rows by unordered ID pair -------------------------------------------
    groups: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    names_by_id: Dict[str, Counter] = defaultdict(Counter)
    for idx, r in enumerate(raw_rows):
        if r.quarantine_reason:
            continue
        a, b = _id_num(r.id_a.strip()), _id_num(r.id_b.strip())
        groups[(min(a, b), max(a, b))].append(idx)
        names_by_id[r.id_a.strip()][clean_display_name(r.name_a)] += 1
        names_by_id[r.id_b.strip()][clean_display_name(r.name_b)] += 1

    # --- drug master (one row per DDInter ID) --------------------------------------------
    pair_count: Counter = Counter()
    for (lo, hi) in groups:
        pair_count[f"DDInter{lo}"] += 1
        pair_count[f"DDInter{hi}"] += 1

    name_conflicts = 0
    for ddid in sorted(names_by_id, key=lambda s: _id_num(s)):
        counter = names_by_id[ddid]
        # most frequent spelling; ties broken alphabetically for determinism
        primary = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        if len(counter) > 1:
            name_conflicts += 1
            res.issues.append(
                {
                    "dataset": "ddinter", "check_name": "id_with_multiple_names", "severity": "warning",
                    "affected": 1, "total": 1, "detail": f"{ddid}: {sorted(counter)}",
                }
            )
        base, qual = split_qualifier(primary)
        res.drugs.append(
            {
                "ddinter_id": ddid,
                "id_num": _id_num(ddid),
                "name_original": primary,
                "name_variants_json": json.dumps(sorted(counter)) if len(counter) > 1 else None,
                "name_base": base,
                "qualifier": qual,
                "qualifier_kind": qualifier_kind(qual),
                "name_normalized": normalize_name(primary),
                "ingredient_normalized": normalize_name(base),
                "n_pairs": pair_count[ddid],
            }
        )
    drug_name = {d["ddinter_id"]: d["name_original"] for d in res.drugs}

    # --- pairs ----------------------------------------------------------------------------
    stats_status: Counter = Counter()
    reversed_dupes = same_dir_dupes = conflicts = 0
    for pair_id, key in enumerate(sorted(groups), start=1):
        lo, hi = key
        idxs = groups[key]
        rows = [raw_rows[i] for i in idxs]
        levels = sorted({CANONICAL_LEVELS[r.level.strip().lower()] for r in rows})
        orientations = {(_id_num(r.id_a.strip()), _id_num(r.id_b.strip())) for r in rows}
        if len(rows) > 1:
            if len(orientations) > 1:
                reversed_dupes += 1
            else:
                same_dir_dupes += 1

        labelled = [lv for lv in levels if lv in LABELLED_LEVELS]
        if len(labelled) == 1:
            severity, status = labelled[0], "labeled"
            resolution = "single_value" if len(levels) == 1 else "labelled_value_kept_over_unknown"
        elif len(labelled) == 0:
            severity, status, resolution = None, "unknown", "source_level_unknown"
        else:
            severity, status, resolution = None, "conflict", "conflicting_levels_unresolved"
            conflicts += 1
            res.issues.append(
                {
                    "dataset": "ddinter", "check_name": "pair_level_conflict", "severity": "error",
                    "affected": 1, "total": 1,
                    "detail": f"DDInter{lo}|DDInter{hi}: {levels} in {sorted({r.file for r in rows})}",
                }
            )
        stats_status[status] += 1
        res.pairs.append(
            {
                "pair_id": pair_id,
                "id_lo": f"DDInter{lo}", "id_hi": f"DDInter{hi}",
                "num_lo": lo, "num_hi": hi,
                "drug_lo": drug_name[f"DDInter{lo}"], "drug_hi": drug_name[f"DDInter{hi}"],
                "severity_label": severity,
                "severity_levels_seen": ";".join(levels),
                "label_status": status,
                "resolution": resolution,
                "n_source_rows": len(rows),
                "source_files": ";".join(sorted({r.file for r in rows})),
                "evidence_id": f"DDInter{lo} | DDInter{hi}",
                # DDInter CSVs carry NO interaction text. The legacy JSON copied the severity
                # into the description field; we do not carry that echo forward.
                "description": None,
            }
        )
        for i in idxs:
            res.pair_row_map.append((i, pair_id))

    quarantined = Counter(r.quarantine_reason for r in raw_rows if r.quarantine_reason)
    for reason, n in sorted(quarantined.items()):
        res.issues.append(
            {
                "dataset": "ddinter", "check_name": f"quarantined_{reason}", "severity": "error",
                "affected": n, "total": len(raw_rows), "detail": "kept in ddinter_raw, excluded from pairs",
            }
        )
    if reversed_dupes:
        res.issues.append(
            {
                "dataset": "ddinter", "check_name": "reversed_duplicate_pairs_merged", "severity": "info",
                "affected": reversed_dupes, "total": len(res.pairs),
                "detail": "same unordered pair listed as (A,B) and (B,A); merged into one pair",
            }
        )
    if same_dir_dupes:
        res.issues.append(
            {
                "dataset": "ddinter", "check_name": "same_direction_duplicate_pairs_merged", "severity": "info",
                "affected": same_dir_dupes, "total": len(res.pairs), "detail": "exact repeated pair merged",
            }
        )

    res.stats = {
        "raw_rows": len(raw_rows),
        "quarantined_rows": sum(quarantined.values()),
        "quarantine_reasons": dict(quarantined),
        "pairs": len(res.pairs),
        "drugs": len(res.drugs),
        "pairs_by_status": dict(stats_status),
        "pairs_by_label": dict(Counter(p["severity_label"] or "NULL" for p in res.pairs)),
        "reversed_duplicate_pairs": reversed_dupes,
        "same_direction_duplicate_pairs": same_dir_dupes,
        "level_conflicts": conflicts,
        "ids_with_multiple_names": name_conflicts,
        "drugs_with_qualifier": sum(1 for d in res.drugs if d["qualifier"]),
        "distinct_normalized_names": len({d["name_normalized"] for d in res.drugs}),
    }
    return res
