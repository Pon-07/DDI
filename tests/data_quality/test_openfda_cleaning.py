"""Unit tests for openFDA cleaning using small SYNTHETIC rows."""

import pandas as pd

from data_pipeline.openfda import REQUIRED_COLUMNS, clean_openfda


def frame(rows):
    base = {c: "" for c in REQUIRED_COLUMNS}
    return pd.DataFrame([{**base, **r} for r in rows], dtype=str)


def test_unnamed_label_is_kept_but_quarantined_and_name_not_invented():
    out = clean_openfda(frame([{"id": "L1", "dosage_and_administration": "Take 1 tablet daily",
                                "renal_related": "False", "hepatic_related": "False"}]), "openfda")
    lab = out["labels"][0]
    assert lab["name_status"] == "unnamed" and lab["primary_name"] is None
    assert lab["ingestible"] == 0 and lab["quarantine_reason"] == "missing_drug_name_in_source_extract"
    assert len(out["sections"]) == 1  # text still preserved


def test_named_label_with_section_is_ingestible():
    out = clean_openfda(frame([{"id": "L1", "generic_name": "FAKEDRUGOL", "contraindications": "None known",
                                "renal_related": "True", "hepatic_related": "False"}]), "openfda")
    lab = out["labels"][0]
    assert lab["ingestible"] == 1 and lab["primary_name"] == "FAKEDRUGOL"
    assert (lab["renal_flag"], lab["hepatic_flag"]) == (1, 0)


def test_generic_preferred_over_brand_and_brand_used_as_fallback():
    out = clean_openfda(frame([
        {"id": "A", "generic_name": "gen", "brand_name": "brand", "drug_interactions": "x"},
        {"id": "B", "brand_name": "brandonly", "drug_interactions": "x"}]), "openfda")
    assert [l["primary_name"] for l in out["labels"]] == ["gen", "brandonly"]


def test_placeholder_names_count_as_missing():
    out = clean_openfda(frame([{"id": "A", "generic_name": "nan", "brand_name": "None", "drug_interactions": "x"}]),
                        "openfda")
    assert out["labels"][0]["name_status"] == "unnamed"


def test_whitespace_only_sections_are_not_sections_and_text_is_verbatim():
    text = "  Keep   exact\n spacing  "
    out = clean_openfda(frame([{"id": "A", "generic_name": "g", "dosage_and_administration": "   ",
                                "contraindications": text}]), "openfda")
    assert [s["section"] for s in out["sections"]] == ["contraindications"]
    assert out["sections"][0]["text"] == text
    assert out["sections"][0]["char_len"] == len(text)


def test_multi_product_names_flagged_not_split():
    out = clean_openfda(frame([{"id": "A", "generic_name": "ONE | TWO", "drug_interactions": "x"}]), "openfda")
    assert out["labels"][0]["multi_product"] == 1 and out["labels"][0]["primary_name"] == "ONE | TWO"


def test_non_boolean_flag_is_reported():
    out = clean_openfda(frame([{"id": "A", "generic_name": "g", "drug_interactions": "x",
                                "renal_related": "maybe"}]), "openfda")
    assert out["labels"][0]["renal_flag"] is None
    assert any(i["check_name"] == "non_boolean_renal_hepatic_flag" for i in out["issues"])


def test_duplicate_label_ids_are_reported_and_first_kept():
    out = clean_openfda(frame([{"id": "A", "generic_name": "first", "drug_interactions": "x"},
                               {"id": "A", "generic_name": "second", "drug_interactions": "y"}]), "openfda")
    assert [l["generic_name"] for l in out["labels"]] == ["first"]
    assert out["stats"]["duplicate_ids_skipped"] == 1
