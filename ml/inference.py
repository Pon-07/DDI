"""Numpy-only inference for the severity baseline (no scikit-learn, no pickle at runtime).

The artifact is a plain JSON file (vocabulary, idf weights, logistic-regression coefficients), so loading it
cannot execute code. `ml/train_final.py` produces it and asserts that this module reproduces scikit-learn's
probabilities, so train/serve skew cannot creep in unnoticed.

The model estimates the severity of an interaction that is ALREADY KNOWN to exist. It was trained on DDInter
interacting pairs only (no negative examples), so it cannot say whether two drugs interact.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

LABELS = ("Minor", "Moderate", "Major")
ARTIFACT_FORMAT = "aegis-severity-linear-v2"
SUPPORTED_FORMATS = ("aegis-severity-linear-v1", ARTIFACT_FORMAT)
_WS = re.compile(r"\s\s+")


def char_wb_ngrams(text: str, ngram_range: Tuple[int, int] = (3, 4)) -> List[str]:
    """Same analyzer as scikit-learn's TfidfVectorizer(analyzer='char_wb', lowercase=True)."""
    text = _WS.sub(" ", (text or "").lower())
    lo, hi = ngram_range
    out: List[str] = []
    for word in text.split():
        word = " " + word + " "
        n_chars = len(word)
        for n in range(lo, hi + 1):
            offset = 0
            out.append(word[offset:offset + n])
            while offset + n < n_chars:
                offset += 1
                out.append(word[offset:offset + n])
            if offset == 0:  # word shorter than n
                break
    return out


@dataclass
class FeatureContribution:
    ngram: str
    contribution: float
    drugs: List[str]


@dataclass
class PairPrediction:
    label: str
    probabilities: Dict[str, float]
    confidence: float
    top_features: List[FeatureContribution] = field(default_factory=list)
    runner_up: Optional[str] = None
    margin: float = 0.0


class SeverityModel:
    def __init__(self, artifact: dict):
        if artifact.get("format") not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported model artifact format: {artifact.get('format')!r}")
        vec = artifact["vectorizer"]
        self.meta = artifact["metadata"]
        self.validation = artifact.get("validation", {})
        self.ngram_range = tuple(vec["ngram_range"])
        self.vocab: Dict[str, int] = vec["vocabulary"]
        self.terms: List[str] = [""] * len(self.vocab)
        for term, idx in self.vocab.items():
            self.terms[idx] = term
        self.idf = np.asarray(vec["idf"], dtype=np.float64)
        atc = artifact.get("atc") or {}
        self.atc_vocab: Dict[str, int] = atc.get("vocabulary", {})
        self.atc_terms: List[str] = [""] * len(self.atc_vocab)
        for tok, idx in self.atc_vocab.items():
            self.atc_terms[idx] = "ATC:" + tok
        self.atc_drug_tokens: Dict[str, List[str]] = atc.get("drug_tokens", {})   # training drugs, as trained
        self.atc_by_name: Dict[str, List[str]] = atc.get("by_name", {})           # any drug named in the ATC index
        self.K = len(self.atc_vocab)
        self.coef = np.asarray(artifact["classifier"]["coef"], dtype=np.float64)       # (3, 2(V+K))
        self.intercept = np.asarray(artifact["classifier"]["intercept"], dtype=np.float64)
        self.classes = tuple(artifact["classifier"]["classes"])
        self.V = len(self.vocab)
        self.D = self.V + self.K
        if self.coef.shape != (len(self.classes), 2 * self.D) or tuple(self.classes) != LABELS:
            raise ValueError("Model artifact is internally inconsistent")
        names = artifact.get("training_drugs", [])
        self.seen_drugs = frozenset(names)
        self._pair_labels: Dict[Tuple[str, str], int] = {
            (names[i], names[j]): y for i, j, y in artifact.get("training_pairs", [])}

    @classmethod
    def load(cls, path: Path) -> "SeverityModel":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    # --- features -------------------------------------------------------------------------------
    def atc_vec(self, name_text: str) -> np.ndarray:
        """Binary ATC level 1-4 indicators (L2-normalised); zeros if the drug has no known ATC code."""
        vec = np.zeros(self.K, dtype=np.float64)
        toks = self.atc_drug_tokens.get(name_text)
        if toks is None:
            toks = self.atc_by_name.get(name_text, [])
        for t in toks:
            idx = self.atc_vocab.get(t)
            if idx is not None:
                vec[idx] = 1.0
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec

    def drug_vec(self, name_text: str) -> np.ndarray:
        v = self.tfidf(name_text)
        return np.concatenate([v, self.atc_vec(name_text)]) if self.K else v

    def has_atc(self, name_text: str) -> bool:
        return bool(self.K and self.atc_vec(name_text).any())

    def tfidf(self, name_text: str) -> np.ndarray:
        """Sublinear-tf * idf, L2-normalised; identical to the training vectoriser."""
        vec = np.zeros(self.V, dtype=np.float64)
        counts: Dict[int, int] = {}
        for gram in char_wb_ngrams(name_text, self.ngram_range):
            idx = self.vocab.get(gram)
            if idx is not None:
                counts[idx] = counts.get(idx, 0) + 1
        for idx, tf in counts.items():
            vec[idx] = (1.0 + math.log(tf)) * self.idf[idx]
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec

    def _logits(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        x = np.concatenate([a + b, a * b])  # symmetric in (A, B)
        return self.coef @ x + self.intercept

    @staticmethod
    def _softmax(z: np.ndarray) -> np.ndarray:
        e = np.exp(z - z.max())
        return e / e.sum()

    def predict_proba(self, name_a: str, name_b: str) -> np.ndarray:
        return self._softmax(self._logits(self.drug_vec(name_a), self.drug_vec(name_b)))

    # --- prediction + explanation ---------------------------------------------------------------
    def predict(self, name_a: str, name_b: str, top_k: int = 5) -> PairPrediction:
        a, b = self.drug_vec(name_a), self.drug_vec(name_b)
        logits = self._logits(a, b)
        proba = self._softmax(logits)
        order = np.argsort(-proba)
        best, second = int(order[0]), int(order[1])
        # exact additive decomposition of the winning logit over n-grams:
        #   coef[c,k]*(a_k+b_k) + coef[c,V+k]*a_k*b_k      (+ intercept, not attributed to any feature)
        contrib = self.coef[best, : self.D] * (a + b) + self.coef[best, self.D:] * (a * b)
        terms = self.terms + self.atc_terms
        feats: List[FeatureContribution] = []
        for k in np.argsort(-contrib)[:top_k]:
            if contrib[k] <= 0:
                break
            drugs = [n for n, v in ((name_a, a), (name_b, b)) if v[k] > 0]
            feats.append(FeatureContribution(terms[k], float(contrib[k]), drugs))
        return PairPrediction(
            label=self.classes[best],
            probabilities={c: float(p) for c, p in zip(self.classes, proba)},
            confidence=float(proba[best]),
            top_features=feats,
            runner_up=self.classes[second],
            margin=float(proba[best] - proba[second]),
        )

    def training_label(self, name_a: str, name_b: str) -> Optional[int]:
        """Class index DDInter gave this pair in the training data (-1 = conflicting), or None if it was not trained on."""
        key = (name_a, name_b) if name_a <= name_b else (name_b, name_a)
        return self._pair_labels.get(key)

    def knows(self, name_text: str) -> bool:
        return name_text in self.seen_drugs
