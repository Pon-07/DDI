"""Train the deployable severity baseline and write its artifact + validation report.

    python -m ml.train_final --clean-dir datasets/clean

* Same model family and features as the audited baseline (`ml/experiment.py`): balanced logistic regression on
  symmetric pair features built from character n-grams of the normalised drug-ingredient name.
* Validation is done BEFORE the final fit, on three regimes that mirror real use (see `ml/splits_regimes`):
  both drugs seen / one drug unseen / both drugs unseen. Numbers come from held-out pairs, never training pairs.
* The shipped artifact is then fit on ALL labelled pairs; it has no held-out set of its own, so the quoted
  numbers describe this recipe, not a second, independent test of the shipped weights.
* The artifact is JSON (no pickle). `ml.inference` is checked against scikit-learn to 1e-9 before writing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import scipy.sparse as sp
import sklearn
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, f1_score, recall_score

from ml.data import LABELS, build_drug_table, load_pairs
from ml.inference import ARTIFACT_FORMAT, SeverityModel
from ml.splits import assert_drug_disjoint

NGRAM_RANGE = (3, 4)
MAX_FEATURES = 4000
C = 1.0
BANDS = [(0.0, 0.5, "low"), (0.5, 0.7, "medium"), (0.7, 1.01, "high")]
REGIMES = ("both_seen", "one_unseen", "both_unseen")
MODEL_VERSION = "severity-baseline-v1"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fit(pairs: pd.DataFrame, drugs: pd.DataFrame, train_idx: np.ndarray, seed: int = 0):
    ids = sorted(set(pairs.iloc[train_idx]["id_lo"]) | set(pairs.iloc[train_idx]["id_hi"]))
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=NGRAM_RANGE, max_features=MAX_FEATURES,
                          lowercase=True, sublinear_tf=True).fit(drugs.loc[ids, "name_text"])
    clf = LogisticRegression(max_iter=400, class_weight="balanced", C=C)
    clf.fit(_X(pairs.iloc[train_idx], drugs, vec), pairs.iloc[train_idx]["y"].to_numpy())
    return vec, clf


def _X(pairs: pd.DataFrame, drugs: pd.DataFrame, vec) -> sp.csr_matrix:
    a = vec.transform(drugs.loc[pairs["id_lo"], "name_text"])
    b = vec.transform(drugs.loc[pairs["id_hi"], "name_text"])
    return sp.hstack([a + b, a.multiply(b)], format="csr")


def regime_split(pairs: pd.DataFrame, seed: int, drug_frac: float = 0.2, pair_frac: float = 0.2):
    """Train on pairs among 'seen' drugs minus a random pair hold-out. Test on three disjoint sets:
    both_seen   - held-out pairs whose two drugs both appear in training (new pair, known drugs)
    one_unseen  - pairs with exactly one held-out drug
    both_unseen - pairs with both drugs held out (cold start)"""
    rng = np.random.default_rng(seed)
    drugs = np.array(sorted(set(pairs["id_lo"]) | set(pairs["id_hi"])))
    held = set(rng.choice(drugs, size=int(len(drugs) * drug_frac), replace=False))
    lo, hi = pairs["id_lo"].isin(held).to_numpy(), pairs["id_hi"].isin(held).to_numpy()
    seen_pairs = np.where(~lo & ~hi)[0]
    perm = rng.permutation(seen_pairs)
    n_t = int(len(perm) * pair_frac)
    test_seen, train = perm[:n_t], perm[n_t:]
    tests = {"both_seen": test_seen, "one_unseen": np.where(lo ^ hi)[0], "both_unseen": np.where(lo & hi)[0]}
    # drugs of a test set that are claimed 'unseen' must not occur in training
    train_ids = set(pairs.iloc[train]["id_lo"]) | set(pairs.iloc[train]["id_hi"])
    assert not (held & train_ids)
    assert_drug_disjoint(pairs, train, tests["both_unseen"])
    return train, tests


def _metrics(y, p) -> Dict:
    return {
        "n": int(len(y)),
        "macro_f1": float(f1_score(y, p, average="macro", labels=[0, 1, 2], zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y, p)),
        "recall": {LABELS[i]: float(r) for i, r in enumerate(recall_score(y, p, labels=[0, 1, 2], average=None, zero_division=0))},
    }


def cross_validate(pairs, drugs, seeds: int) -> Dict:
    per_regime: Dict[str, Dict[str, List]] = {r: {"model": [], "majority": [], "stratified": []} for r in REGIMES}
    pooled = {r: {"y": [], "pred": [], "conf": []} for r in REGIMES}
    for seed in range(seeds):
        train, tests = regime_split(pairs, seed)
        vec, clf = _fit(pairs, drugs, train, seed)
        y_tr = pairs.iloc[train]["y"].to_numpy()
        maj = DummyClassifier(strategy="most_frequent").fit(np.zeros((len(train), 1)), y_tr)
        strat = DummyClassifier(strategy="stratified", random_state=seed).fit(np.zeros((len(train), 1)), y_tr)
        for regime, idx in tests.items():
            if len(idx) == 0:
                continue
            sub = pairs.iloc[idx]
            y = sub["y"].to_numpy()
            proba = clf.predict_proba(_X(sub, drugs, vec))
            pred = proba.argmax(1)
            per_regime[regime]["model"].append(_metrics(y, pred))
            per_regime[regime]["majority"].append(_metrics(y, maj.predict(np.zeros((len(y), 1)))))
            per_regime[regime]["stratified"].append(_metrics(y, strat.predict(np.zeros((len(y), 1)))))
            pooled[regime]["y"].append(y); pooled[regime]["pred"].append(pred); pooled[regime]["conf"].append(proba.max(1))
    summary, reliability = {}, {}
    for regime in REGIMES:
        summary[regime] = {}
        for name, runs in per_regime[regime].items():
            summary[regime][name] = {
                "n_test_pairs_per_seed": [r["n"] for r in runs],
                "macro_f1": {"mean": float(np.mean([r["macro_f1"] for r in runs])), "std": float(np.std([r["macro_f1"] for r in runs]))},
                "balanced_accuracy": {"mean": float(np.mean([r["balanced_accuracy"] for r in runs])), "std": float(np.std([r["balanced_accuracy"] for r in runs]))},
                "recall": {l: float(np.mean([r["recall"][l] for r in runs])) for l in LABELS},
            }
        y = np.concatenate(pooled[regime]["y"]); pred = np.concatenate(pooled[regime]["pred"]); conf = np.concatenate(pooled[regime]["conf"])
        base = {LABELS[c]: float((y == c).mean()) for c in range(3)}
        table = []
        for lo_, hi_, band in BANDS:
            m = (conf >= lo_) & (conf < hi_)
            for c in range(3):
                sel = m & (pred == c)
                table.append({"band": band, "predicted": LABELS[c], "n": int(sel.sum()),
                              "precision": float((y[sel] == c).mean()) if sel.sum() >= 20 else None,
                              "base_rate": base[LABELS[c]]})
        reliability[regime] = table
    return {"summary": summary, "reliability": reliability}


def build_artifact(pairs, drugs, clean_dir: Path, seeds: int, cv: Dict) -> dict:
    all_idx = np.arange(len(pairs))
    vec, clf = _fit(pairs, drugs, all_idx)
    vocab = {t: int(i) for t, i in vec.vocabulary_.items()}
    training_drugs = sorted(set(drugs.loc[sorted(set(pairs["id_lo"]) | set(pairs["id_hi"])), "name_text"]))
    artifact = {
        "format": ARTIFACT_FORMAT,
        "metadata": {
            "model_name": MODEL_VERSION,
            "model_family": "balanced multinomial logistic regression on symmetric pair features",
            "features": "TF-IDF of character 3-4-grams of the normalised ingredient name; pair = [a+b, a*b]",
            "trained_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sklearn_version": sklearn.__version__,
            "n_training_pairs": int(len(pairs)),
            "n_training_drugs": len(training_drugs),
            "class_counts": {l: int((pairs["y"] == i).sum()) for i, l in enumerate(LABELS)},
            "training_data": {
                "ddinter_pairs_clean.csv_sha256": _sha256(clean_dir / "ddinter_pairs_clean.csv"),
                "ddinter_drugs_clean.csv_sha256": _sha256(clean_dir / "ddinter_drugs_clean.csv"),
                "source": "DDInter category A only; `Unknown` pairs excluded (not labels)",
            },
            "intended_use": "advisory estimate of the severity of an interaction that is already documented",
            "not_for": ["deciding whether two drugs interact (no negative examples)",
                        "overriding or replacing rule-based findings", "dosing or treatment decisions"],
            "hyperparameters": {"ngram_range": list(NGRAM_RANGE), "max_features": MAX_FEATURES, "C": C,
                                "class_weight": "balanced", "validation_seeds": seeds},
        },
        "vectorizer": {"ngram_range": list(NGRAM_RANGE), "vocabulary": vocab,
                       "idf": [round(float(x), 12) for x in vec.idf_]},
        "classifier": {"classes": list(LABELS),
                       "coef": [[round(float(v), 12) for v in row] for row in clf.coef_],
                       "intercept": [round(float(v), 12) for v in clf.intercept_]},
        "training_drugs": training_drugs,
        "training_pairs": _training_pairs(pairs, drugs, training_drugs),
        "validation": cv,
    }
    return artifact, vec, clf


def _training_pairs(pairs, drugs, training_drugs) -> list:
    """[[i, j, y], ...] over indices into `training_drugs` (i <= j). y is the class index, or -1 if DDInter
    gives different levels to pairs that share these normalised names (e.g. a drug and its ophthalmic form).
    Lets the advisor say honestly that a pair was part of the training data."""
    idx = {n: i for i, n in enumerate(training_drugs)}
    seen: Dict = {}
    for r in pairs.itertuples():
        i, j = sorted((idx[drugs.loc[r.id_lo, "name_text"]], idx[drugs.loc[r.id_hi, "name_text"]]))
        seen.setdefault((i, j), set()).add(int(r.y))
    return [[i, j, (next(iter(ys)) if len(ys) == 1 else -1)] for (i, j), ys in sorted(seen.items())]


def verify_equivalence(artifact: dict, pairs, drugs, vec, clf, n: int = 300) -> float:
    model = SeverityModel(artifact)
    rng = np.random.default_rng(7)
    sample = pairs.iloc[rng.choice(len(pairs), size=min(n, len(pairs)), replace=False)]
    sk = clf.predict_proba(_X(sample, drugs, vec))
    mine = np.vstack([model.predict_proba(drugs.loc[r.id_lo, "name_text"], drugs.loc[r.id_hi, "name_text"]) for r in sample.itertuples()])
    swapped = np.vstack([model.predict_proba(drugs.loc[r.id_hi, "name_text"], drugs.loc[r.id_lo, "name_text"]) for r in sample.itertuples()])
    gap = float(np.abs(sk - mine).max())
    assert gap < 1e-9, f"inference differs from scikit-learn by {gap}"
    assert float(np.abs(mine - swapped).max()) < 1e-12, "prediction is not symmetric in (A, B)"
    return gap


def render_md(art: dict, gap: float) -> str:
    m, v = art["metadata"], art["validation"]["summary"]
    L = [f"# {m['model_name']} - validation report", "",
         f"Trained {m['trained_at_utc']} on **{m['n_training_pairs']:,}** labelled DDInter pairs ({m['n_training_drugs']:,} drugs): {m['class_counts']}.",
         "", "**Advisory only. Not clinically validated.** Features are drug-name text only (no RxCUI/ATC/CYP450/interaction text). "
         "The model estimates severity for an interaction that already exists; it was trained without negative examples "
         "and cannot tell whether two drugs interact. Chance macro-F1 for 3 classes is ~0.33.", "",
         "## Held-out results (mean ± std over %d seeds)" % m["hyperparameters"]["validation_seeds"], "",
         "| regime | what it means | model macro-F1 | majority | random | recall Minor / Moderate / Major |", "|---|---|---|---|---|---|"]
    meaning = {"both_seen": "new pair, both drugs seen in training", "one_unseen": "one drug never seen",
               "both_unseen": "cold start: neither drug seen"}
    for r in REGIMES:
        s = v[r]["model"]
        L.append(f"| `{r}` | {meaning[r]} | {s['macro_f1']['mean']:.3f} ± {s['macro_f1']['std']:.3f} | "
                 f"{v[r]['majority']['macro_f1']['mean']:.3f} | {v[r]['stratified']['macro_f1']['mean']:.3f} | "
                 f"{s['recall']['Minor']:.2f} / {s['recall']['Moderate']:.2f} / {s['recall']['Major']:.2f} |")
    L += ["", "## Reliability of a prediction (pooled held-out pairs)", "",
          "Share of held-out pairs that truly had the predicted label, by confidence band, next to how common that label is.", ""]
    for r in REGIMES:
        L += [f"### `{r}`", "", "| band | predicted | n | precision | base rate |", "|---|---|---|---|---|"]
        for row in art["validation"]["reliability"][r]:
            p = "n/a (n<20)" if row["precision"] is None else f"{row['precision']:.2f}"
            L.append(f"| {row['band']} | {row['predicted']} | {row['n']} | {p} | {row['base_rate']:.2f} |")
        L.append("")
    L += ["## Notes", "",
          f"- Runtime inference (`ml/inference.py`, numpy) matches scikit-learn to {gap:.1e} and is exactly symmetric in (A, B).",
          "- The shipped weights are fit on all labelled pairs; they have no held-out set of their own.",
          "- `both_seen` numbers reflect drug-identity signal; they say nothing about pharmacology.",
          "- Only DDInter category A is available (1 of 8 files), so coverage of drug classes is partial.", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean-dir", type=Path, default=Path("datasets/clean"))
    ap.add_argument("--artifact", type=Path, default=Path("ml/artifacts/severity_baseline_v1.json"))
    ap.add_argument("--report-dir", type=Path, default=Path("ml/reports"))
    ap.add_argument("--seeds", type=int, default=5)
    a = ap.parse_args(argv)
    pairs, drugs = load_pairs(a.clean_dir), build_drug_table(a.clean_dir)
    cv = cross_validate(pairs, drugs, a.seeds)
    artifact, vec, clf = build_artifact(pairs, drugs, a.clean_dir, a.seeds, cv)
    gap = verify_equivalence(artifact, pairs, drugs, vec, clf)
    a.artifact.parent.mkdir(parents=True, exist_ok=True)
    a.artifact.write_text(json.dumps(artifact, separators=(",", ":")), encoding="utf-8")
    a.report_dir.mkdir(parents=True, exist_ok=True)
    (a.report_dir / "severity_model_validation.json").write_text(json.dumps({"metadata": artifact["metadata"], "validation": cv}, indent=2), encoding="utf-8")
    md = render_md(artifact, gap)
    (a.report_dir / "severity_model_validation.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
