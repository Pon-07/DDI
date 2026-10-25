"""WHO ATC/DDD index reading and exact-name matching to DDInter drugs.

* The ATC file is read verbatim (header-checked); nothing is dropped.
* Only 7-character codes (chemical substances) are matched to drugs, by EXACT equality of the repo-standard
  normalised name. No fuzzy matching: a wrong ATC code would be worse than a missing one. A drug can match
  several ATC codes (different routes/indications); all are kept. Combination products ("X and Y") never match
  a single DDInter drug.
* Unmatched drugs keep NULL. Nothing is invented.

Licence note: the WHO ATC/DDD index is copyright WHO Collaborating Centre; check its terms before
redistributing the raw file or derived mappings.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from data_pipeline.names import normalize_name

EXPECTED_HEADER = ["atc_code", "atc_name", "ddd", "uom", "adm_r", "note"]


def read_atc(path: Path) -> List[Dict[str, str]]:
    text = Path(path).read_bytes().decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = [h.strip() for h in next(reader, [])]
    if header != EXPECTED_HEADER:
        raise ValueError(f"{Path(path).name}: unexpected header {header!r}, expected {EXPECTED_HEADER!r}")
    rows = []
    for n, rec in enumerate(reader, start=2):
        if not rec:
            continue
        if len(rec) != len(EXPECTED_HEADER):
            raise ValueError(f"{Path(path).name}: line {n} has {len(rec)} columns")
        rows.append(dict(zip(EXPECTED_HEADER, (x.strip() for x in rec))))
    return rows


def match_drugs(drugs: List[dict], atc_rows: List[Dict[str, str]]) -> Tuple[Dict[str, List[str]], Dict[str, int]]:
    """-> ({ddinter_id: [atc_code, ...]}, stats)"""
    index: Dict[str, List[str]] = defaultdict(list)
    n_substance = 0
    for r in atc_rows:
        if len(r["atc_code"]) == 7:
            n_substance += 1
            key = normalize_name(r["atc_name"])
            if key and r["atc_code"] not in index[key]:
                index[key].append(r["atc_code"])
    matched: Dict[str, List[str]] = {}
    for d in drugs:
        codes: List[str] = []
        for key in (d["ingredient_normalized"], d["name_normalized"]):
            for c in index.get(key, []):
                if c not in codes:
                    codes.append(c)
            if codes:
                break
        if codes:
            matched[d["ddinter_id"]] = sorted(codes)
    return matched, {"atc_rows": len(atc_rows), "atc_substance_rows": n_substance,
                     "drugs": len(drugs), "drugs_matched": len(matched)}
