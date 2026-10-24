"""Run the baseline comparison and write ml/reports/baseline_report.{json,md}.

    python -m ml.experiment --clean-dir datasets/clean --seeds 5
No model is saved or deployed; this measures how much signal the CURRENT data holds.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, confusion_matrix, f1_score, recall_score

from ml.data import LABELS, build_drug_table, load_pairs
from ml.features import PairFeaturizer
from ml.splits import assert_drug_disjoint, drug_disjoint_split, random_pair_split


def _metrics(y_true, y_pred) -> Dict:
    return {
        "accuracy": float((y_true == y_pred).mean()),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", labels=[0, 1, 2], zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "recall": {LABELS[i]: float(r) for i, r in
                   enumerate(recall_score(y_true, y_pred, labels=[0, 1, 2], average=None, zero_division=0))},
        "confusion": confusion_matrix(y_true, y_pred, labels=[0, 1, 2]).tolist(),
    }


def make_models(seed: int):
    return {
        "majority_class": lambda: DummyClassifier(strategy="most_frequent"),
        "stratified_random_guess": lambda: DummyClassifier(strategy="stratified", random_state=seed),
        "logreg_balanced": lambda: LogisticRegression(max_iter=400, class_weight="balanced", C=1.0),
    }


def run_one(pairs, drugs, split_name: str, seed: int, test_frac=0.2) -> Dict:
    if split_name == "random_pairs":
        tr, te = random_pair_split(pairs, test_frac, seed)
    else:
        tr, te = drug_disjoint_split(pairs, test_frac, seed)
        assert_drug_disjoint(pairs, tr, te)
    train_ids = set(pairs.iloc[tr]["id_lo"]) | set(pairs.iloc[tr]["id_hi"])
    out = {"n_train": int(len(tr)), "n_test": int(len(te)), "models": {}}
    y_tr, y_te = pairs.iloc[tr]["y"].to_numpy(), pairs.iloc[te]["y"].to_numpy()
    ablations = {
        "name_ngrams+label_flags": dict(use_name_ngrams=True, use_label_flags=True),
        "name_ngrams_only": dict(use_name_ngrams=True, use_label_flags=False),
        "label_flags_only": dict(use_name_ngrams=False, use_label_flags=True),
    }
    for mname, mk in make_models(seed).items():
        if mname != "logreg_balanced":
            m = mk().fit(np.zeros((len(tr), 1)), y_tr)
            out["models"][mname] = _metrics(y_te, m.predict(np.zeros((len(te), 1))))
            continue
        for aname, kw in ablations.items():
            fz = PairFeaturizer(drugs, **kw).fit(train_ids)
            m = mk().fit(fz.transform(pairs.iloc[tr]), y_tr)
            out["models"][f"{mname}[{aname}]"] = _metrics(y_te, m.predict(fz.transform(pairs.iloc[te])))
    return out


def summarize(runs: List[Dict]) -> Dict:
    names = runs[0]["models"].keys()
    summ = {}
    for n in names:
        s = {}
        for k in ("accuracy", "macro_f1", "balanced_accuracy"):
            v = [r["models"][n][k] for r in runs]
            s[k] = {"mean": float(np.mean(v)), "std": float(np.std(v))}
        s["recall"] = {l: float(np.mean([r["models"][n]["recall"][l] for r in runs])) for l in LABELS}
        summ[n] = s
    return summ


def render_md(res: Dict) -> str:
    L = ["# Baseline severity experiment (no model deployed)", "",
         f"Labelled pairs: **{res['n_labelled_pairs']:,}** {res['class_counts']} | drugs: {res['n_drugs']:,} | seeds: {res['seeds']}", "",
         "Only DDInter category A is available; features are drug-name character n-grams and openFDA keyword flags. "
         "There is no RxCUI/ATC/CYP450/interaction text. Chance for macro-F1 on 3 classes is ~0.33.", ""]
    for split, d in res["splits"].items():
        L += [f"## Split: `{split}`", f"train/test pairs (seed 0): {d['runs'][0]['n_train']:,} / {d['runs'][0]['n_test']:,}", "",
              "| model | accuracy | macro-F1 | balanced acc | recall Minor | recall Moderate | recall Major |", "|---|---|---|---|---|---|---|"]
        for n, s in d["summary"].items():
            L.append(f"| {n} | {s['accuracy']['mean']:.3f} ± {s['accuracy']['std']:.3f} | {s['macro_f1']['mean']:.3f} ± {s['macro_f1']['std']:.3f} | "
                     f"{s['balanced_accuracy']['mean']:.3f} | {s['recall']['Minor']:.2f} | {s['recall']['Moderate']:.2f} | {s['recall']['Major']:.2f} |")
        L.append("")
    L += ["## How to read this", "",
          "- `random_pairs` lets the same drug appear in train and test, so it overstates how well the model generalises. Do not quote it as performance.",
          "- `drug_disjoint` is the honest number: test drugs are never seen in training (new-drug / cold-start).",
          "- Accuracy is misleading here because the majority class is ~79.5% Moderate; judge by macro-F1 and per-class recall.", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean-dir", type=Path, default=Path("datasets/clean"))
    ap.add_argument("--out-dir", type=Path, default=Path("ml/reports"))
    ap.add_argument("--seeds", type=int, default=5)
    a = ap.parse_args(argv)
    pairs, drugs = load_pairs(a.clean_dir), build_drug_table(a.clean_dir)
    res = {"n_labelled_pairs": int(len(pairs)), "n_drugs": int(len(set(pairs.id_lo) | set(pairs.id_hi))),
           "class_counts": pairs["severity_label"].value_counts().to_dict(), "seeds": a.seeds, "splits": {}}
    for split in ("random_pairs", "drug_disjoint"):
        runs = [run_one(pairs, drugs, split, s) for s in range(a.seeds)]
        res["splits"][split] = {"runs": runs, "summary": summarize(runs)}
    a.out_dir.mkdir(parents=True, exist_ok=True)
    (a.out_dir / "baseline_report.json").write_text(json.dumps(res, indent=2))
    (a.out_dir / "baseline_report.md").write_text(render_md(res))
    print(render_md(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
