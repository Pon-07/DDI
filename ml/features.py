"""Symmetric pair features: f(A,B) == f(B,A). Vocabulary/scalers are fit on TRAIN drugs only."""
from __future__ import annotations

from typing import Iterable, Tuple

import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.feature_extraction.text import TfidfVectorizer

from ml.data import NUMERIC_COLS


class PairFeaturizer:
    def __init__(self, drugs: pd.DataFrame, use_name_ngrams: bool = True, use_label_flags: bool = True,
                 ngram_range: Tuple[int, int] = (3, 4), max_features: int = 4000):
        self.drugs = drugs
        self.use_name_ngrams, self.use_label_flags = use_name_ngrams, use_label_flags
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=ngram_range, max_features=max_features,
                                   lowercase=True, sublinear_tf=True)

    def fit(self, train_drug_ids: Iterable[str]) -> "PairFeaturizer":
        if self.use_name_ngrams:
            self.vec.fit(self.drugs.loc[sorted(set(train_drug_ids)), "name_text"])
        return self

    def _drug_matrix(self, ids) -> sp.csr_matrix:
        parts = []
        sub = self.drugs.loc[list(ids)]
        if self.use_name_ngrams:
            parts.append(self.vec.transform(sub["name_text"]))
        if self.use_label_flags:
            parts.append(sp.csr_matrix(sub[NUMERIC_COLS].to_numpy(dtype=float)))
        return sp.hstack(parts, format="csr")

    def transform(self, pairs: pd.DataFrame) -> sp.csr_matrix:
        a, b = self._drug_matrix(pairs["id_lo"]), self._drug_matrix(pairs["id_hi"])
        # sum and elementwise product are symmetric in (A, B)
        return sp.hstack([a + b, a.multiply(b)], format="csr")
