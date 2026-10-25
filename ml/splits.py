"""Pair-splitting strategies. Random pair splits leak drug identity; the cold-start split does not."""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def random_pair_split(pairs: pd.DataFrame, test_frac: float, seed: int) -> Tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(pairs))
    n_test = int(len(pairs) * test_frac)
    return idx[n_test:], idx[:n_test]


def drug_disjoint_split(pairs: pd.DataFrame, test_frac: float, seed: int) -> Tuple[np.ndarray, np.ndarray]:
    """Cold-start (S3): hold out a set of DRUGS. Train = pairs with both drugs in the train set,
    test = pairs with both drugs held out. Pairs mixing the two sets are discarded, so no test drug
    is ever seen (in any pair) during training."""
    drugs = np.array(sorted(set(pairs["id_lo"]) | set(pairs["id_hi"])))
    rng = np.random.default_rng(seed)
    held = set(rng.choice(drugs, size=max(1, int(len(drugs) * test_frac)), replace=False))
    lo_held = pairs["id_lo"].isin(held).to_numpy()
    hi_held = pairs["id_hi"].isin(held).to_numpy()
    train = np.where(~lo_held & ~hi_held)[0]
    test = np.where(lo_held & hi_held)[0]
    return train, test


def assert_drug_disjoint(pairs: pd.DataFrame, train: np.ndarray, test: np.ndarray) -> None:
    tr = set(pairs.iloc[train]["id_lo"]) | set(pairs.iloc[train]["id_hi"])
    te = set(pairs.iloc[test]["id_lo"]) | set(pairs.iloc[test]["id_hi"])
    shared = tr & te
    assert not shared, f"{len(shared)} drugs appear in both train and test"
