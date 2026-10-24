"""RxNorm (RxCUI) and ClinPGx (ATC top-up) cross-references for DDInter drugs.

Rules (same philosophy as data_pipeline/atc.py):
* EXACT equality of the repo-standard normalised name only. No fuzzy matching, no guessing.
* RxNorm: ingredient concepts only (TTY IN = ingredient, PIN = precise ingredient). Multiple-ingredient (MIN),
  brand and dose-form concepts are never used. A name that maps to more than one distinct RxCUI is AMBIGUOUS and is left
  empty (counted in the stats), never resolved by picking one.
* ClinPGx: used (a) for an RxCUI when RxNorm gave none and (b) for ATC 7-character substance codes only for drugs the WHO
  file did not match. A name must identify exactly one ClinPGx drug record (drug classes are ignored), otherwise skipped.
* Every value keeps its source in the staging DB; unmatched drugs stay NULL.

Licences: RxNorm is subject to NLM/UMLS terms; ClinPGx is CC BY-NC-SA 4.0. Neither raw file is committed to this repo.
"""
from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from data_pipeline.names import normalize_name

RXNORM_TTYS = ("IN", "PIN")
csv.field_size_limit(10_000_000)  # some ClinPGx cells (SMILES/InChI/cross-references) exceed the 128 KB default


def _open_member(path: Path, member_suffix: str):
    """Text stream of `path` (a plain file, or the first member of a zip whose name ends with member_suffix)."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(path)
        names = [n for n in zf.namelist() if n.replace("\\", "/").endswith(member_suffix)]
        if not names:
            raise ValueError(f"{path.name}: no member ending in {member_suffix!r}")
        return io.TextIOWrapper(zf.open(names[0]), encoding="utf-8", newline=""), zf
    return open(path, encoding="utf-8", newline=""), None


def read_rxnorm_ingredients(path: Path) -> Dict[str, Dict[str, Set[str]]]:
    """-> {normalised name: {"IN": {rxcui,...}, "PIN": {rxcui,...}}} from RXNCONSO.RRF (or the RxNorm zip)."""
    out: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: {"IN": set(), "PIN": set()})
    fh, zf = _open_member(path, "RXNCONSO.RRF")
    try:
        for line in fh:
            p = line.rstrip("\n").split("|")
            if len(p) < 15 or p[1] != "ENG" or p[11] != "RXNORM" or p[12] not in RXNORM_TTYS:
                continue
            key = normalize_name(p[14])
            if key:
                out[key][p[12]].add(p[0])
    finally:
        fh.close()
        if zf:
            zf.close()
    return dict(out)


def match_rxnorm(drugs: List[dict], index: Dict[str, Dict[str, Set[str]]]) -> Tuple[Dict[str, Tuple[str, str]], Dict[str, int]]:
    """-> ({ddinter_id: (rxcui, tty)}, stats). IN beats PIN; more than one distinct RxCUI at the winning level = ambiguous."""
    matched: Dict[str, Tuple[str, str]] = {}
    ambiguous = 0
    for d in drugs:
        got = None
        for key in (d["ingredient_normalized"], d["name_normalized"]):
            ent = index.get(key)
            if not ent:
                continue
            for tty in RXNORM_TTYS:
                ids = ent[tty]
                if len(ids) == 1:
                    got = (next(iter(ids)), tty)
                    break
                if len(ids) > 1:
                    ambiguous += 1
                    got = False
                    break
            if got is not None:
                break
        if got:
            matched[d["ddinter_id"]] = got
    return matched, {"rxnorm_names_indexed": len(index), "drugs": len(drugs), "rxcui_matched": len(matched),
                     "rxcui_ambiguous_skipped": ambiguous}


# ---------------------------------------------------------------------------------------- ClinPGx ----
def read_clinpgx_drugs(path: Path) -> List[Dict[str, str]]:
    fh, zf = _open_member(path, "drugs.tsv")
    try:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    finally:
        fh.close()
        if zf:
            zf.close()
    need = {"PharmGKB Accession Id", "Name", "Generic Names", "Type", "RxNorm Identifiers", "ATC Identifiers"}
    if rows and not need <= set(rows[0]):
        raise ValueError(f"ClinPGx drugs.tsv is missing columns {sorted(need - set(rows[0]))}")
    return rows


def _split_ids(value: str) -> List[str]:
    return [x.strip() for x in (value or "").replace(";", ",").split(",") if x.strip()]


def _is_single_drug(rec: Dict[str, str]) -> bool:
    t = rec["Type"]
    return t.startswith("Drug") and "Class" not in t


def build_clinpgx_index(rows: Iterable[Dict[str, str]]) -> Dict[str, Set[str]]:
    index: Dict[str, Set[str]] = defaultdict(set)
    for r in rows:
        if not _is_single_drug(r):
            continue
        names = [r["Name"]] + [x.strip() for x in r["Generic Names"].replace('"', "").split(",")]
        for n in names:
            key = normalize_name(n) if n else ""
            if key:
                index[key].add(r["PharmGKB Accession Id"])
    return dict(index)


def clinpgx_topup(drugs: List[dict], rows: List[Dict[str, str]], have_rxcui: Set[str], have_atc: Set[str]
                  ) -> Tuple[Dict[str, str], Dict[str, List[str]], Dict[str, int]]:
    """-> (extra rxcui {id: rxcui}, extra atc {id: [codes]}, stats) for drugs lacking them."""
    by_id = {r["PharmGKB Accession Id"]: r for r in rows}
    index = build_clinpgx_index(rows)
    rx: Dict[str, str] = {}
    atc: Dict[str, List[str]] = {}
    ambiguous = 0
    for d in drugs:
        did = d["ddinter_id"]
        if did in have_rxcui and did in have_atc:
            continue
        ids: Set[str] = set()
        for key in (d["ingredient_normalized"], d["name_normalized"]):
            ids = index.get(key, set())
            if ids:
                break
        if not ids:
            continue
        if len(ids) > 1:
            ambiguous += 1
            continue
        rec = by_id[next(iter(ids))]
        if did not in have_rxcui:
            ids_rx = _split_ids(rec["RxNorm Identifiers"])
            if len(ids_rx) == 1 and ids_rx[0].isdigit():
                rx[did] = ids_rx[0]
        if did not in have_atc:
            codes = sorted({c for c in _split_ids(rec["ATC Identifiers"]) if len(c) == 7 and c[0].isalpha()})
            if codes:
                atc[did] = codes
    return rx, atc, {"clinpgx_single_drug_names": len(index), "clinpgx_name_ambiguous_skipped": ambiguous,
                     "clinpgx_rxcui_added": len(rx), "clinpgx_atc_added": len(atc)}
