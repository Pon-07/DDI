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
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.preprocessing import normalize
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
MODEL_VERSION = "severity-baseline-v2.1"
PUBLIC_ARTIFACT = Path("ml/artifacts/severity_baseline_v2.json")                       # name-only, committed
RESTRICTED_ARTIFACT = Path("ml/artifacts/restricted/severity_baseline_v2_atc.json")    # name + WHO ATC, git-ignored


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class FeatureSpace:
    """Per-drug features fit on TRAIN drugs only: char n-gram TF-IDF of the name, optionally + binary ATC level 1-4
    indicators (L2-normalised). A pair is [a+b, a*b], symmetric in (A, B)."""

    def __init__(self, use_atc: bool):
        self.use_atc = use_atc
        self.name_vec = TfidfVectorizer(analyzer="char_wb", ngram_range=NGRAM_RANGE, max_features=MAX_FEATURES,
                                        lowercase=True, sublinear_tf=True)
        self.atc_vec = CountVectorizer(binary=True, token_pattern=r"\S+", lowercase=False)

    def fit(self, drugs: pd.DataFrame, ids) -> "FeatureSpace":
        self.name_vec.fit(drugs.loc[ids, "name_text"])
        toks = drugs.loc[ids, "atc_tokens"]
        if self.use_atc and (toks != "").any():
            self.atc_vec.fit(toks)
        else:
            self.use_atc = False
        return self

    def drug_matrix(self, drugs: pd.DataFrame, ids) -> sp.csr_matrix:
        parts = [self.name_vec.transform(drugs.loc[ids, "name_text"])]
        if self.use_atc:
            parts.append(sp.csr_matrix(normalize(self.atc_vec.transform(drugs.loc[ids, "atc_tokens"]).astype(float))))
        return sp.hstack(parts, format="csr")

    def X(self, pairs: pd.DataFrame, drugs: pd.DataFrame) -> sp.csr_matrix:
        a, b = self.drug_matrix(drugs, pairs["id_lo"]), self.drug_matrix(drugs, pairs["id_hi"])
        return sp.hstack([a + b, a.multiply(b)], format="csr")


def _fit(pairs: pd.DataFrame, drugs: pd.DataFrame, train_idx: np.ndarray, seed: int = 0, use_atc: bool = False):
    ids = sorted(set(pairs.iloc[train_idx]["id_lo"]) | set(pairs.iloc[train_idx]["id_hi"]))
    fs = FeatureSpace(use_atc).fit(drugs, ids)
    clf = LogisticRegression(max_iter=400, class_weight="balanced", C=C)
    clf.fit(_X(pairs.iloc[train_idx], drugs, fs), pairs.iloc[train_idx]["y"].to_numpy())
    return fs, clf


def _X(pairs: pd.DataFrame, drugs: pd.DataFrame, fs: "FeatureSpace") -> sp.csr_matrix:
    return fs.X(pairs, drugs)


def regime_split(pairs: pd.DataFrame, seed: int, drug_frac: float = 0.2, pair_frac: float = 0.2, groups=None):
    """Train on pairs among 'seen' drugs minus a random pair hold-out. Test on three disjoint sets:
    both_seen   - held-out pairs whose two drugs both appear in training (new pair, known drugs)
    one_unseen  - pairs with exactly one held-out drug
    both_unseen - pairs with both drugs held out (cold start)

    groups: optional mapping DDInter id -> group key (the ingredient the model actually sees). DDInter lists the same
    ingredient under several ids (route/form variants). Holding out ids only would leave a sibling of an 'unseen' drug in
    training with IDENTICAL features, so with groups whole ingredients are held out together, and the both_seen hold-out is
    drawn per ingredient pair so a variant pair cannot sit in both train and test."""
    rng = np.random.default_rng(seed)
    key = (lambda i: i) if groups is None else (lambda i: groups.get(i, i) or i)
    ids = np.array(sorted(set(pairs["id_lo"]) | set(pairs["id_hi"])))
    gkeys = np.array(sorted({key(i) for i in ids}))
    held_groups = set(rng.choice(gkeys, size=int(len(gkeys) * drug_frac), replace=False))
    held = {i for i in ids if key(i) in held_groups}
    lo, hi = pairs["id_lo"].isin(held).to_numpy(), pairs["id_hi"].isin(held).to_numpy()
    seen_pairs = np.where(~lo & ~hi)[0]
    pk = np.array([f"{min(key(a), key(b))}||{max(key(a), key(b))}" for a, b in
                   zip(pairs.iloc[seen_pairs]["id_lo"], pairs.iloc[seen_pairs]["id_hi"])])
    uniq = np.array(sorted(set(pk)))
    test_keys = set(rng.choice(uniq, size=int(len(uniq) * pair_frac), replace=False))
    in_test = np.array([k in test_keys for k in pk])
    test_seen, train = seen_pairs[in_test], seen_pairs[~in_test]
    tests = {"both_seen": test_seen, "one_unseen": np.where(lo ^ hi)[0], "both_unseen": np.where(lo & hi)[0]}
    # drugs of a test set that are claimed 'unseen' must not occur in training
    train_ids = set(pairs.iloc[train]["id_lo"]) | set(pairs.iloc[train]["id_hi"])
    assert not (held & train_ids)
    assert_drug_disjoint(pairs, train, tests["both_unseen"])
    if groups is not None:
        train_groups = {key(i) for i in train_ids}
        assert not (held_groups & train_groups), "an ingredient is both held out and in training"
        train_pk = {f"{min(key(a), key(b))}||{max(key(a), key(b))}" for a, b in
                    zip(pairs.iloc[train]["id_lo"], pairs.iloc[train]["id_hi"])}
        assert not (test_keys & train_pk), "an ingredient pair is in both train and test"
    return train, tests


def _metrics(y, p) -> Dict:
    return {
        "n": int(len(y)),
        "macro_f1": float(f1_score(y, p, average="macro", labels=[0, 1, 2], zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y, p)),
        "recall": {LABELS[i]: float(r) for i, r in enumerate(recall_score(y, p, labels=[0, 1, 2], average=None, zero_division=0))},
    }


def cross_validate(pairs, drugs, seeds: int, use_atc: bool = False, grouped: bool = True) -> Dict:
    groups = drugs["group_key"].to_dict() if grouped else None
    per_regime: Dict[str, Dict[str, List]] = {r: {"model": [], "majority": [], "stratified": []} for r in REGIMES}
    pooled = {r: {"y": [], "pred": [], "conf": []} for r in REGIMES}
    for seed in range(seeds):
        train, tests = regime_split(pairs, seed, groups=groups)
        vec, clf = _fit(pairs, drugs, train, seed, use_atc)
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


def build_artifact(pairs, drugs, clean_dir: Path, seeds: int, cv: Dict, use_atc: bool = False,
                   who_lookup=None, comparison=None) -> dict:
    all_idx = np.arange(len(pairs))
    fs, clf = _fit(pairs, drugs, all_idx, 0, use_atc)
    vec = fs.name_vec
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
                "source": "",
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
    if fs.use_atc:
        in_train = drugs.loc[sorted(set(pairs["id_lo"]) | set(pairs["id_hi"]))]
        artifact["atc"] = {
            "vocabulary": {t: int(i) for t, i in fs.atc_vec.vocabulary_.items()},
            "drug_tokens": {n: g.split() for n, g in zip(in_train["name_text"], in_train["atc_tokens"]) if g},
            "by_name": who_lookup or {},
        }
        artifact["metadata"]["features"] = ("TF-IDF of character 3-4-grams of the normalised ingredient name + binary "
                                            "WHO ATC level 1-4 indicators (exact-name match; zeros when unknown); pair = [a+b, a*b]")
        artifact["metadata"]["training_data"]["source"] = "DDInter categories A,B,D,H,L,P,R,V; `Unknown` pairs excluded (not labels)"
    else:
        artifact["metadata"]["training_data"]["source"] = "DDInter categories A,B,D,H,L,P,R,V; `Unknown` pairs excluded (not labels)"
    artifact["metadata"]["uses_who_atc_data"] = bool(fs.use_atc)
    if comparison is not None:
        artifact["metadata"]["feature_comparison"] = comparison
    return artifact, fs, clf


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
         "", "**Advisory only. Not clinically validated.** Features: " + m["features"] + ". No RxCUI/CYP450/interaction-text features (RxCUI is stored as an identifier only). "
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
    cmp_ = m.get("feature_comparison")
    if cmp_:
        L += ["", "## Feature comparison (same splits, same seeds)", "",
              "| features | both_seen | one_unseen | both_unseen (cold start) |", "|---|---|---|---|"]
        for name, res in cmp_["results"].items():
            L.append(f"| {name} | " + " | ".join(f"{res[r]['mean']:.3f} ± {res[r]['std']:.3f}" for r in REGIMES) + " |")
        L += ["", f"Decision: **{cmp_['decision']}**", ""]
        if cmp_.get("split_policy"):
            L += ["## Split policy (shipped features)", "",
                  "DDInter lists some ingredients under several ids (route/form variants) and the model sees only the ingredient, so "
                  "per-id splits leave a sibling of an 'unseen' drug in training. The reported numbers hold out whole ingredients "
                  "(same RxCUI or same name together) and whole ingredient pairs.", "",
                  "| split | both_seen | one_unseen | both_unseen (cold start) |", "|---|---|---|---|"]
            for name, res in cmp_["split_policy"].items():
                L.append(f"| {name} | " + " | ".join(f"{res[r]['mean']:.3f} ± {res[r]['std']:.3f}" for r in REGIMES) + " |")
            L.append("")
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
          *(["- ATC codes come from an exact-name match to the WHO ATC/DDD index; drugs without a match have no ATC features. "
             "This artifact contains WHO-derived data and is NOT for redistribution until the WHO licence question is settled."]
            if m.get("uses_who_atc_data") else
            ["- This public artifact uses drug-name text only and contains no WHO-derived data (no ATC codes, tokens or lookups)."]),
          "- All 8 DDInter category files are used, but DDInter is still the only label source and has no negative pairs.", ""]
    return "\n".join(L)


def _atc_wins(results) -> bool:
    base, atc_ = results["name n-grams only"], results["name n-grams + ATC levels 1-4"]
    cold = atc_["both_unseen"]["mean"] - base["both_unseen"]["mean"] > max(atc_["both_unseen"]["std"], base["both_unseen"]["std"])
    no_loss = all(atc_[r]["mean"] >= base[r]["mean"] - max(atc_[r]["std"], base[r]["std"]) for r in REGIMES)
    return bool(cold and no_loss)


def _default_atc_file():
    found = sorted(Path("data_validation").glob("WHO_ATC-DDD*.csv"))
    return found[-1] if found else None


def _who_lookup(path):
    """normalised ATC substance name -> ATC level 1-4 tokens, for drugs that are not in DDInter (cold start).
    Derived from the WHO index; check WHO's terms before redistributing the artifact."""
    if path is None or not Path(path).exists():
        return {}
    from data_pipeline.atc import read_atc
    from data_pipeline.names import normalize_name
    from ml.data import atc_tokens
    out = {}
    for r in read_atc(path):
        if len(r["atc_code"]) == 7:
            key = normalize_name(r["atc_name"])
            if key:
                out.setdefault(key, set()).update(atc_tokens(r["atc_code"]).split())
    return {k: sorted(v) for k, v in sorted(out.items())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean-dir", type=Path, default=Path("datasets/clean"))
    ap.add_argument("--variant", choices=["public", "restricted"], default="public",
                    help="public (default): name-only model, no WHO-derived data anywhere in the artifact; written to the committed paths. "
                         "restricted: name + WHO ATC model, written to git-ignored ml/artifacts/restricted/ (not for redistribution "
                         "until the WHO licence question is settled)")
    ap.add_argument("--artifact", type=Path, default=None)
    ap.add_argument("--features", choices=["auto", "name", "name_atc"], default=None,
                    help="default: name for --variant public, name_atc for --variant restricted")
    ap.add_argument("--report-dir", type=Path, default=None)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--atc-file", type=Path, default=None, help="WHO ATC/DDD csv used for the name->ATC lookup embedded in the artifact")
    a = ap.parse_args(argv)
    restricted = a.variant == "restricted"
    a.features = a.features or ("name_atc" if restricted else "name")
    a.artifact = a.artifact or (RESTRICTED_ARTIFACT if restricted else PUBLIC_ARTIFACT)
    a.report_dir = a.report_dir or (Path("ml/reports/restricted") if restricted else Path("ml/reports"))
    if not restricted and a.features != "name":
        raise SystemExit("--variant public must use --features name (no WHO-derived data in committed artifacts)")
    pairs, drugs = load_pairs(a.clean_dir), build_drug_table(a.clean_dir, use_restricted=restricted)
    cv_name = cross_validate(pairs, drugs, a.seeds, use_atc=False)
    has_atc = bool((drugs["atc_tokens"] != "").any())
    cv_atc = cross_validate(pairs, drugs, a.seeds, use_atc=True) if has_atc else None
    results = {"name n-grams only": {r: cv_name["summary"][r]["model"]["macro_f1"] for r in REGIMES}}
    if cv_atc:
        results["name n-grams + ATC levels 1-4"] = {r: cv_atc["summary"][r]["model"]["macro_f1"] for r in REGIMES}
    use_atc = a.features == "name_atc" or (a.features == "auto" and cv_atc is not None and _atc_wins(results))
    if a.features == "name_atc" and not has_atc:
        raise SystemExit("no ATC data in datasets/clean/ddinter_drugs_clean.csv")
    decision = ("ship name + ATC" if use_atc else "ship name-only") + f" (--features {a.features})"
    cv = cv_atc if use_atc else cv_name
    # how much the earlier per-id splits flattered the numbers (same features, same seeds)
    cv_ids = cross_validate(pairs, drugs, a.seeds, use_atc=use_atc, grouped=False)
    split_cmp = {"ingredient-grouped splits (this model)": {r: cv["summary"][r]["model"]["macro_f1"] for r in REGIMES},
                 "per-DDInter-id splits (previous reports)": {r: cv_ids["summary"][r]["model"]["macro_f1"] for r in REGIMES}}
    who_lookup = _who_lookup(a.atc_file or _default_atc_file()) if use_atc else None
    artifact, vec, clf = build_artifact(pairs, drugs, a.clean_dir, a.seeds, cv, use_atc=use_atc, who_lookup=who_lookup,
                                        comparison={"results": results, "decision": decision, "split_policy": split_cmp})
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
