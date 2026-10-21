"""Ingredient-grouped regime splits: variants of one drug (several DDInter ids) never straddle train and test."""
import itertools

import numpy as np
import pandas as pd

from ml.train_final import regime_split


def _synthetic():
    # 40 ingredients; every 4th ingredient has a second DDInter id (a route/form variant) with the same group key
    groups, ids = {}, []
    for g in range(40):
        ids.append(f"D{g}"); groups[f"D{g}"] = f"n:g{g}"
        if g % 4 == 0:
            ids.append(f"V{g}"); groups[f"V{g}"] = f"n:g{g}"
    rows = [{"id_lo": a, "id_hi": b, "y": (i % 3)} for i, (a, b) in enumerate(itertools.combinations(ids, 2))]
    return pd.DataFrame(rows), groups


def test_variants_of_an_ingredient_stay_on_one_side():
    pairs, groups = _synthetic()
    for seed in range(5):
        train, tests = regime_split(pairs, seed, groups=groups)
        tr = pairs.iloc[train]
        train_groups = {groups[i] for i in set(tr.id_lo) | set(tr.id_hi)}
        for regime in ("one_unseen", "both_unseen"):
            te = pairs.iloc[tests[regime]]
            unseen = [i for i in set(te.id_lo) | set(te.id_hi) if groups[i] not in train_groups]
            assert unseen, regime
        cold = pairs.iloc[tests["both_unseen"]]
        assert not ({groups[i] for i in set(cold.id_lo) | set(cold.id_hi)} & train_groups)
        # no ingredient pair is both trained on and tested on (variant pairs included)
        key = lambda d: {tuple(sorted((groups[a], groups[b]))) for a, b in zip(d.id_lo, d.id_hi)}
        assert not (key(tr) & key(pairs.iloc[tests["both_seen"]]))


def test_per_id_split_would_leak_variants_which_is_why_grouping_exists():
    pairs, groups = _synthetic()
    leaks = 0
    for seed in range(5):
        train, tests = regime_split(pairs, seed)            # legacy behaviour: ids only
        tr = pairs.iloc[train]
        train_groups = {groups[i] for i in set(tr.id_lo) | set(tr.id_hi)}
        cold = pairs.iloc[tests["both_unseen"]]
        leaks += len({groups[i] for i in set(cold.id_lo) | set(cold.id_hi)} & train_groups)
    assert leaks > 0
