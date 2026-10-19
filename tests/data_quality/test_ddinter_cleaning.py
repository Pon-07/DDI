"""Unit tests for DDInter cleaning rules using small SYNTHETIC rows (clearly fake drug names)."""

import pytest

from data_pipeline.ddinter import RawRow, clean_ddinter, read_ddinter_csv


def row(line, ida, na, idb, nb, level, file="synthetic.csv"):
    return RawRow(file=file, line_no=line, fields=[ida, na, idb, nb, level],
                  id_a=ida, name_a=na, id_b=idb, name_b=nb, level=level)


def test_reversed_pair_is_merged_not_duplicated():
    res = clean_ddinter([row(2, "DDInter1", "Drugone", "DDInter2", "Drugtwo", "Major", "A.csv"),
                         row(2, "DDInter2", "Drugtwo", "DDInter1", "Drugone", "Major", "B.csv")])
    assert len(res.pairs) == 1
    p = res.pairs[0]
    assert (p["id_lo"], p["id_hi"], p["severity_label"], p["n_source_rows"]) == ("DDInter1", "DDInter2", "Major", 2)
    assert res.stats["reversed_duplicate_pairs"] == 1
    assert len(res.pair_row_map) == 2  # both raw rows stay linked: nothing lost


def test_conflicting_levels_get_no_label():
    res = clean_ddinter([row(2, "DDInter1", "X", "DDInter2", "Y", "Major"),
                         row(3, "DDInter2", "Y", "DDInter1", "X", "Minor")])
    p = res.pairs[0]
    assert p["label_status"] == "conflict" and p["severity_label"] is None
    assert p["severity_levels_seen"] == "Major;Minor"
    assert any(i["check_name"] == "pair_level_conflict" for i in res.issues)


def test_unknown_is_not_a_label():
    p = clean_ddinter([row(2, "DDInter1", "X", "DDInter2", "Y", "Unknown")]).pairs[0]
    assert p["label_status"] == "unknown" and p["severity_label"] is None


def test_labelled_level_beats_unknown_for_same_pair():
    p = clean_ddinter([row(2, "DDInter1", "X", "DDInter2", "Y", "Unknown"),
                       row(3, "DDInter2", "Y", "DDInter1", "X", "Moderate")]).pairs[0]
    assert p["label_status"] == "labeled" and p["severity_label"] == "Moderate"
    assert p["resolution"] == "labelled_value_kept_over_unknown"


@pytest.mark.parametrize(
    "r, reason",
    [
        (row(2, "DDInter1", "X", "DDInter1", "X", "Major"), "self_pair"),
        (row(2, "BAD", "X", "DDInter2", "Y", "Major"), "malformed_ddinter_id"),
        (row(2, "DDInter1", " ", "DDInter2", "Y", "Major"), "empty_drug_name"),
        (row(2, "DDInter1", "X", "DDInter2", "Y", "Severe"), "invalid_level"),
    ],
)
def test_bad_rows_are_quarantined_not_dropped(r, reason):
    res = clean_ddinter([r])
    assert res.raw_rows[0].quarantine_reason == reason
    assert res.pairs == [] and len(res.raw_rows) == 1  # kept in raw


def test_level_case_is_canonicalised_but_original_kept_raw():
    r = row(2, "DDInter1", "X", "DDInter2", "Y", "mAjOr")
    res = clean_ddinter([r])
    assert res.pairs[0]["severity_label"] == "Major"
    assert res.raw_rows[0].level == "mAjOr"


def test_description_is_never_populated():
    assert clean_ddinter([row(2, "DDInter1", "X", "DDInter2", "Y", "Major")]).pairs[0]["description"] is None


def test_qualifier_variants_remain_separate_drugs():
    res = clean_ddinter([row(2, "DDInter1", "Atropine", "DDInter3", "Z", "Minor"),
                         row(3, "DDInter2", "Atropine (ophthalmic)", "DDInter3", "Z", "Major")])
    by_id = {d["ddinter_id"]: d for d in res.drugs}
    assert by_id["DDInter2"]["qualifier"] == "ophthalmic" and by_id["DDInter2"]["qualifier_kind"] == "route_form"
    assert by_id["DDInter1"]["ingredient_normalized"] == by_id["DDInter2"]["ingredient_normalized"]
    assert len(res.pairs) == 2  # not merged


def test_output_order_is_deterministic():
    rows = [row(2, "DDInter9", "N", "DDInter3", "C", "Minor"), row(3, "DDInter1", "A", "DDInter2", "B", "Major")]
    a = clean_ddinter(list(rows))
    b = clean_ddinter(list(reversed(rows)))
    assert [(p["id_lo"], p["id_hi"]) for p in a.pairs] == [(p["id_lo"], p["id_hi"]) for p in b.pairs]


def test_header_mismatch_raises(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected header"):
        read_ddinter_csv(f)


def test_wrong_column_count_is_kept_and_quarantined(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("DDInterID_A,Drug_A,DDInterID_B,Drug_B,Level\nDDInter1,X,DDInter2,Y\n", encoding="utf-8")
    rows = read_ddinter_csv(f)
    assert len(rows) == 1 and rows[0].quarantine_reason == "wrong_column_count_4"
    assert clean_ddinter(rows).pairs == []
