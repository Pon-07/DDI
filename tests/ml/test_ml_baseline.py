"""Leakage and correctness tests for the ML baseline. Synthetic, invented drug names only."""
import numpy as np
import pandas as pd
import pytest

from ml.data import build_drug_table, load_pairs
from ml.features import PairFeaturizer
from ml.splits import assert_drug_disjoint, drug_disjoint_split, random_pair_split

NAMES = ["fakedrugol", "zorbamine", "quenatrix", "pelvadone", "mirkotin", "lanvorex", "tuxapril", "dolmegran",
         "ventarol", "brisafen", "calmotril", "oxenadine"]


@pytest.fixture()
def clean_dir(tmp_path):
    ids = [f"DDInter{i}" for i in range(1, len(NAMES) + 1)]
    drugs = pd.DataFrame({"ddinter_id": ids, "name_original": NAMES, "name_base": NAMES, "qualifier": "",
                          "qualifier_kind": "", "name_normalized": NAMES, "ingredient_normalized": NAMES,
                          "n_pairs": 1, "rxcui": "", "atc_codes": "", "cyp_profile": "", "drugbank_id": ""})
    drugs.to_csv(tmp_path / "ddinter_drugs_clean.csv", index=False)
    rows, pid = [], 0
    levels = ["Major", "Moderate", "Minor", "Moderate"]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            pid += 1
            st = "unknown" if (i + j) % 7 == 0 else "labeled"
            rows.append(dict(pair_id=pid, id_lo=ids[i], id_hi=ids[j], drug_lo=NAMES[i], drug_hi=NAMES[j],
                             severity_label="" if st == "unknown" else levels[(i * j) % 4], label_status=st))
    pd.DataFrame(rows).to_csv(tmp_path / "ddinter_pairs_clean.csv", index=False)
    pd.DataFrame([dict(label_id="L1", name_normalized="zorbamine", renal_flag="1", hepatic_flag="0",
                       n_sections=3, ingestible="1"),
                  dict(label_id="L2", name_normalized="quenatrix", renal_flag="0", hepatic_flag="1",
                       n_sections=1, ingestible="0")]).to_csv(tmp_path / "openfda_labels_clean.csv", index=False)
    return tmp_path


def test_unknown_pairs_are_never_labels(clean_dir):
    pairs = load_pairs(clean_dir)
    assert set(pairs["severity_label"]) <= {"Minor", "Moderate", "Major"}
    assert (pairs["label_status"] == "labeled").all()
    assert len(pairs) < 66  # 12 drugs -> 66 pairs, some are unknown


def test_label_flags_only_use_ingestible_labels(clean_dir):
    d = build_drug_table(clean_dir)
    assert d.loc["DDInter2", "label_renal"] == 1 and d.loc["DDInter2", "has_label"] == 1
    assert d.loc["DDInter3", "has_label"] == 0  # its only label is not ingestible -> no fabricated link


@pytest.mark.parametrize("seed", range(5))
def test_drug_disjoint_split_has_no_shared_drug(clean_dir, seed):
    pairs = load_pairs(clean_dir)
    tr, te = drug_disjoint_split(pairs, 0.34, seed)
    assert len(tr) and len(te)
    assert_drug_disjoint(pairs, tr, te)
    assert not set(tr) & set(te)


def test_random_split_is_a_partition(clean_dir):
    pairs = load_pairs(clean_dir)
    tr, te = random_pair_split(pairs, 0.25, 0)
    assert sorted(np.concatenate([tr, te])) == list(range(len(pairs)))


def test_features_are_symmetric_in_drug_order(clean_dir):
    pairs, drugs = load_pairs(clean_dir), build_drug_table(clean_dir)
    fz = PairFeaturizer(drugs).fit(drugs.index)
    swapped = pairs.rename(columns={"id_lo": "id_hi", "id_hi": "id_lo"})
    diff = fz.transform(pairs) - fz.transform(swapped)
    assert abs(diff).max() < 1e-12


def test_vocabulary_is_fit_on_train_drugs_only(clean_dir):
    drugs = build_drug_table(clean_dir)
    fz = PairFeaturizer(drugs, ngram_range=(3, 3)).fit(["DDInter1", "DDInter2"])
    vocab = set(fz.vec.vocabulary_)
    assert " fa" in vocab or "fak" in vocab  # from a train drug
    assert "oxe" not in vocab  # n-gram that exists only in a held-out drug
