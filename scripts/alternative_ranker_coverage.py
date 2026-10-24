#!/usr/bin/env python3
"""Reproducible coverage analysis for the alternative ranker over DDInter's Major pairs.

For every DDInter pair labelled Major, and for each of the two drugs in it ("replace X, keep partner Y"), ask:
is there a same-ATC-class candidate, and what is known about that candidate against the partner Y?

Per replaced drug, exactly one category applies (best case across the candidates):
  none          - no same-class candidate (or the replaced drug has no ATC class in the clean data)
  known_lower   - at least one candidate has a KNOWN Minor or Moderate interaction with the partner
  unknown_only  - no known-lower candidate, but at least one candidate whose pair with the partner is UNKNOWN
                  (absent from DDInter, or listed with severity Unknown). Unknown is NOT safe.
  all_major     - every candidate has a known Major interaction with the partner

Scopes: level 4 only; level 4 or 3 (any level); and the ranker's own rule (level 4, plus level 3 only when fewer
than `level3_fallback_min_free_of_major` level-4 candidates are free of a known Major interaction).

Only the partner drug is checked here (one other drug); real patients take more, so real coverage is lower.
ATC class is not clinical equivalence. Input data is read at runtime; the report holds aggregate counts only
(no drug names, no ATC codes).

    python scripts/alternative_ranker_coverage.py [--clean-dir datasets/clean] [--out reports/alternative_ranker/coverage.md]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.resolution.alternatives import RANKER_CONFIG, RANKER_VERSION, DDInterIndex  # noqa: E402

CATEGORIES = ("known_lower", "unknown_only", "all_major", "none")
SCOPES = ("level4", "level4_or_3", "ranker_rule")
SCOPE_TITLES = {
    "level4": "ATC level 4 only",
    "level4_or_3": "ATC level 4 or 3 (any level)",
    "ranker_rule": "Ranker rule (level 4, plus level 3 only if fewer than 3 level-4 candidates are free of a known Major)",
}


def _category(ranks: List[int]) -> str:
    if not ranks:
        return "none"
    if any(r in (1, 2) for r in ranks):
        return "known_lower"
    if any(r == 0 for r in ranks):
        return "unknown_only"
    return "all_major"


def _side_categories(index: DDInterIndex, replaced: int, partner: int, min_free: int) -> Dict[str, Any]:
    ent = index.entities[replaced]
    if not ent.atc:
        return {"no_atc": True, **{s: "none" for s in SCOPES}}
    l4, l3 = index.class_members(ent)

    def ok(m: int) -> bool:
        return m != partner and not index.entities[m].is_combination

    r4 = [index.severity_rank(m, partner) for m in l4 if ok(m)]
    r3 = [index.severity_rank(m, partner) for m in l3 if ok(m)]
    free = sum(1 for r in r4 if r != 3)
    return {
        "no_atc": False,
        "level4": _category(r4),
        "level4_or_3": _category(r4 + r3),
        "ranker_rule": _category(r4 + (r3 if free < min_free else [])),
        "has_l4": bool(r4),
        "has_l3_or_l4": bool(r4 or r3),
    }


def compute_coverage(index: DDInterIndex, pair_rows: List[Dict[str, str]]) -> Dict[str, Any]:
    min_free = int(RANKER_CONFIG["level3_fallback_min_free_of_major"])
    order = {c: i for i, c in enumerate(CATEGORIES)}
    sides = {s: {c: 0 for c in CATEGORIES} for s in SCOPES}
    pairs = {s: {c: 0 for c in CATEGORIES} for s in SCOPES}
    n_rows = n_skipped = n_sides = n_no_atc = n_has_l4 = n_has_any = 0
    for r in pair_rows:
        if (r.get("label_status") or "") != "labeled" or (r.get("severity_label") or "") != "Major":
            continue
        n_rows += 1
        a, b = index.entity_for_id(r.get("id_lo", "")), index.entity_for_id(r.get("id_hi", ""))
        if a is None or b is None or a == b:
            n_skipped += 1
            continue
        res = [_side_categories(index, a, b, min_free), _side_categories(index, b, a, min_free)]
        for side in res:
            n_sides += 1
            n_no_atc += bool(side["no_atc"])
            n_has_l4 += bool(side.get("has_l4"))
            n_has_any += bool(side.get("has_l3_or_l4"))
            for s in SCOPES:
                sides[s][side[s]] += 1
        for s in SCOPES:
            pairs[s][min((x[s] for x in res), key=order.__getitem__)] += 1
    return {"major_rows": n_rows, "rows_skipped": n_skipped, "pairs_analysed": n_rows - n_skipped, "sides": n_sides,
            "sides_no_atc": n_no_atc, "sides_with_l4": n_has_l4, "sides_with_l3_or_l4": n_has_any,
            "per_side": sides, "per_pair": pairs}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def _table(counts: Dict[str, Dict[str, int]], denom: int) -> List[str]:
    lines = ["| Scope | Known lower-severity alternative | Only unknown alternatives | Only known-Major alternatives | No same-class alternative |",
             "|---|---|---|---|---|"]
    for s in SCOPES:
        c = counts[s]
        lines.append(f"| {SCOPE_TITLES[s]} | {c['known_lower']:,} ({_pct(c['known_lower'], denom)}) | "
                     f"{c['unknown_only']:,} ({_pct(c['unknown_only'], denom)}) | {c['all_major']:,} ({_pct(c['all_major'], denom)}) | "
                     f"{c['none']:,} ({_pct(c['none'], denom)}) |")
    return lines


def render_markdown(cov: Dict[str, Any], hashes: Dict[str, str], clean_dir_label: str) -> str:
    sides, pairs = cov["sides"], cov["pairs_analysed"]
    ps, pp = cov["per_side"], cov["per_pair"]
    out = [
        "# Alternative ranker: coverage over DDInter Major pairs",
        "",
        f"Ranker version `{RANKER_VERSION}`. Generated by `scripts/alternative_ranker_coverage.py` "
        f"(`python scripts/alternative_ranker_coverage.py --clean-dir {clean_dir_label}`); deterministic, no timestamps.",
        "",
        "## Read this first",
        "",
        "- **A pair absent from DDInter is NOT safe.** DDInter lists interacting pairs only and the file is sparse, so 'unknown' is the "
        "most common status for a replacement candidate. This report never counts unknown as safe.",
        "- **ATC class is NOT clinical equivalence.** A same-class candidate can differ in route, form, indication, potency or "
        "patient suitability. 'Has an alternative' means 'a same-class drug exists', not 'a suitable replacement exists'.",
        "- Only the original partner drug is checked here. Real patients take more drugs, so real-world coverage is lower.",
        "- Counts describe the data, not clinical validity. Every real suggestion still needs pharmacist review and co-sign.",
        "",
        "## Inputs",
        "",
        f"- `ddinter_drugs_clean.csv` sha256 `{hashes['drugs']}`",
        f"- `ddinter_pairs_clean.csv` sha256 `{hashes['pairs']}`",
        f"- DDInter pairs labelled Major: **{cov['major_rows']:,}** rows "
        f"({cov['rows_skipped']:,} skipped because both ids are variants of the same ingredient or an id is missing; "
        f"**{pairs:,}** analysed).",
        f"- Each Major pair gives two 'replace one drug, keep the other' cases: **{sides:,}** cases "
        f"({cov['sides_no_atc']:,} of them have a replaced drug with no ATC class in the clean data).",
        f"- Cases with at least one same-class candidate: level 4 **{cov['sides_with_l4']:,}** ({_pct(cov['sides_with_l4'], sides)}); "
        f"level 4 or 3 **{cov['sides_with_l3_or_l4']:,}** ({_pct(cov['sides_with_l3_or_l4'], sides)}).",
        "",
        "## Definitions (mutually exclusive, best case across the candidates)",
        "",
        "- **Known lower-severity alternative:** at least one candidate has a known Minor or Moderate interaction with the partner.",
        "- **Only unknown alternatives:** no known-lower candidate, but at least one candidate is unknown with the partner "
        "(absent from DDInter, or listed as Unknown). Unknown is not safe.",
        "- **Only known-Major alternatives:** every candidate has a known Major interaction with the partner.",
        "- **No same-class alternative:** no candidate exists after excluding the pair's own drugs and combination products.",
        "",
        f"## Per replaced drug ({sides:,} cases)",
        "",
        *_table(ps, sides),
        "",
        f"## Per Major pair (best of its two sides; {pairs:,} pairs)",
        "",
        *_table(pp, pairs),
        "",
        "## Method and limits",
        "",
        "- Candidates are drugs sharing an ATC level-4 class (first 5 characters) with the replaced drug; level 3 (first 4 characters) is looser.",
        "- Matching is by exact normalised name / RxCUI groups of DDInter ids; route/form variants are merged and the worst known severity is used.",
        "- Combination products (by name pattern) and the pair's own drugs are not candidates.",
        "- No machine-learning estimate is used here: this is a count over DDInter labels and ATC membership only.",
        "- No drug names or ATC codes are reproduced in this report.",
        "",
    ]
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--clean-dir", default=str(PROJECT_ROOT / "datasets" / "clean"))
    ap.add_argument("--out", default=str(PROJECT_ROOT / "reports" / "alternative_ranker" / "coverage.md"))
    args = ap.parse_args(argv)
    clean = Path(args.clean_dir)
    drugs_p, pairs_p = clean / "ddinter_drugs_clean.csv", clean / "ddinter_pairs_clean.csv"
    with open(drugs_p, encoding="utf-8", newline="") as fh:
        drugs = list(csv.DictReader(fh))
    with open(pairs_p, encoding="utf-8", newline="") as fh:
        pairs = list(csv.DictReader(fh))
    index = DDInterIndex(drugs, pairs)
    cov = compute_coverage(index, pairs)
    try:
        label = str(clean.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        label = "datasets/clean"
    md = render_markdown(cov, {"drugs": _sha256(drugs_p), "pairs": _sha256(pairs_p)}, label.replace("\\", "/"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
