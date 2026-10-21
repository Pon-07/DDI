"""Load the cleaned datasets and build per-drug features. Only real, available columns are used."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

LABELS = ["Minor", "Moderate", "Major"]  # ordinal order
LABEL_TO_INT = {l: i for i, l in enumerate(LABELS)}


def load_pairs(clean_dir: Path) -> pd.DataFrame:
    """Labelled pairs only. `Unknown` is NOT a label and conflicting pairs have no label."""
    df = pd.read_csv(Path(clean_dir) / "ddinter_pairs_clean.csv", dtype=str)
    df = df[(df["label_status"] == "labeled") & df["severity_label"].isin(LABELS)].copy()
    df["y"] = df["severity_label"].map(LABEL_TO_INT).astype(int)
    return df.reset_index(drop=True)


def build_drug_table(clean_dir: Path) -> pd.DataFrame:
    """One row per DDInter drug with name text + label-derived flags (exact normalised-name link only)."""
    clean_dir = Path(clean_dir)
    drugs = pd.read_csv(clean_dir / "ddinter_drugs_clean.csv", dtype=str).fillna("")
    labels = pd.read_csv(clean_dir / "openfda_labels_clean.csv", dtype=str).fillna("")
    labels = labels[labels["ingestible"] == "1"]
    agg = (
        labels.assign(
            renal=lambda d: d["renal_flag"].isin(["1", "True", "true"]).astype(int),
            hepatic=lambda d: d["hepatic_flag"].isin(["1", "True", "true"]).astype(int),
            n_sec=lambda d: pd.to_numeric(d["n_sections"], errors="coerce").fillna(0),
        )
        .groupby("name_normalized")
        .agg(label_renal=("renal", "max"), label_hepatic=("hepatic", "max"),
             label_n_sections=("n_sec", "max"), label_count=("renal", "size"))
    )
    out = drugs.merge(agg, how="left", left_on="name_normalized", right_index=True)
    out["has_label"] = out["label_count"].notna().astype(int)
    for c in ("label_renal", "label_hepatic", "label_n_sections", "label_count"):
        out[c] = out[c].fillna(0)
    out["has_qualifier"] = (out["qualifier"] != "").astype(int)
    if "atc_codes" not in out.columns:
        out["atc_codes"] = ""
    out["atc_tokens"] = out["atc_codes"].fillna("").map(atc_tokens)
    # the text used for char n-grams: normalised ingredient name (no label text, so no leakage of outcomes)
    out["name_text"] = out["ingredient_normalized"].where(out["ingredient_normalized"] != "", out["name_normalized"])
    return out.set_index("ddinter_id")


def atc_tokens(codes: str) -> str:
    """'A02BC01;A02BC05' -> 'A A02 A02B A02BC' (ATC levels 1-4 only; level 5 is the substance itself = identity)."""
    toks = set()
    for code in str(codes or "").split(";"):
        code = code.strip()
        if len(code) == 7:
            toks.update({code[:1], code[:3], code[:4], code[:5]})
    return " ".join(sorted(toks))


NUMERIC_COLS = ["has_label", "label_renal", "label_hepatic", "has_qualifier"]
