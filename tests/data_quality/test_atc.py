"""WHO ATC reading and matching: exact names only, nothing invented, nothing dropped. Synthetic data throughout."""
import pytest

from data_pipeline.atc import EXPECTED_HEADER, match_drugs, read_atc
from data_pipeline.names import normalize_name
from ml.data import atc_tokens


def _write(tmp_path, rows, header=EXPECTED_HEADER):
    p = tmp_path / "atc.csv"
    p.write_text("\n".join([",".join(header)] + [",".join(r) for r in rows]) + "\n", encoding="utf-8")
    return p


def _drug(i, name):
    n = normalize_name(name)
    return {"ddinter_id": i, "ingredient_normalized": n, "name_normalized": n}


def test_reads_rows_verbatim_and_rejects_a_changed_header(tmp_path):
    rows = [["A02BC01", "omeprazole", "20", "mg", "O", ""], ["A02BC", "Proton pump inhibitors", "", "", "", ""]]
    got = read_atc(_write(tmp_path, rows))
    assert len(got) == 2 and got[0]["atc_code"] == "A02BC01" and got[1]["ddd"] == ""
    with pytest.raises(ValueError):
        read_atc(_write(tmp_path, rows, header=["code", "name"]))


def test_only_exact_names_of_seven_character_codes_match(tmp_path):
    rows = [["A02BC01", "omeprazole", "", "", "", ""], ["A02BC05", "esomeprazole", "", "", "", ""],
            ["A02BC", "Proton pump inhibitors", "", "", "", ""],          # group level: never a drug match
            ["N02BE51", "paracetamol and codeine", "", "", "", ""]]     # combination ("X and Y"): never matches a single drug
    atc = read_atc(_write(tmp_path, rows))
    drugs = [_drug("D1", "Omeprazole"), _drug("D2", "Omeprazol"), _drug("D3", "Proton pump inhibitors"),
             _drug("D4", "Paracetamol")]
    matched, stats = match_drugs(drugs, atc)
    assert matched == {"D1": ["A02BC01"]}                       # near-miss spelling and group names stay unmatched
    assert stats["drugs"] == 4 and stats["drugs_matched"] == 1


def test_a_drug_may_keep_several_codes_sorted_and_deduplicated(tmp_path):
    rows = [["S01XA20", "x drug", "", "", "", ""], ["A01AB02", "x drug", "", "", "", ""], ["A01AB02", "x drug", "", "", "", ""]]
    matched, _ = match_drugs([_drug("D1", "x drug")], read_atc(_write(tmp_path, rows)))
    assert matched["D1"] == ["A01AB02", "S01XA20"]


def test_atc_tokens_use_levels_one_to_four_only():
    assert atc_tokens("A02BC01;A02BC05") == "A A02 A02B A02BC"
    assert atc_tokens("") == "" and atc_tokens(None) == ""
    assert atc_tokens("A02BC") == ""                            # not a 7-character substance code
