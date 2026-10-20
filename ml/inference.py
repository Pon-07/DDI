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
ARTIFACT_FORMAT = "aegis-severity-linear-v1"
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
        if artifact.get("format") != ARTIFACT_FORMAT:
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
        self.coef = np.asarray(artifact["classifier"]["coef"], dtype=np.float64)       # (3, 2V)
        self.intercept = np.asarray(artifact["classifier"]["intercept"], dtype=np.float64)
        self.classes = tuple(artifact["classifier"]["classes"])
        self.V = len(self.vocab)
        if self.coef.shape != (len(self.classes), 2 * self.V) or tuple(self.classes) != LABELS:
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
        return self._softmax(self._logits(self.tfidf(name_a), self.tfidf(name_b)))

    # --- prediction + explanation ---------------------------------------------------------------
    def predict(self, name_a: str, name_b: str, top_k: int = 5) -> PairPrediction:
        a, b = self.tfidf(name_a), self.tfidf(name_b)
        logits = self._logits(a, b)
        proba = self._softmax(logits)
        order = np.argsort(-proba)
        best, second = int(order[0]), int(order[1])
        # exact additive decomposition of the winning logit over n-grams:
        #   coef[c,k]*(a_k+b_k) + coef[c,V+k]*a_k*b_k      (+ intercept, not attributed to any feature)
        contrib = self.coef[best, : self.V] * (a + b) + self.coef[best, self.V:] * (a * b)
        feats: List[FeatureContribution] = []
        for k in np.argsort(-contrib)[:top_k]:
            if contrib[k] <= 0:
                break
            drugs = [n for n, v in ((name_a, a), (name_b, b)) if v[k] > 0]
            feats.append(FeatureContribution(self.terms[k], float(contrib[k]), drugs))
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
