"""RxNorm / ClinPGx cross-references: exact names only, ambiguity skipped, nothing invented. Synthetic data only."""
import io
import zipfile

from data_pipeline.crosswalk import (build_clinpgx_index, clinpgx_topup, match_rxnorm, read_clinpgx_drugs,
                                     read_rxnorm_ingredients)
from data_pipeline.names import normalize_name

def _row(rxcui, lat, sab, tty, name):
    # real RXNCONSO layout: RXCUI|LAT|TS|LUI|STT|SUI|ISPREF|RUI|RXAUI|SAUI|SCUI|SAB|TTY|CODE|STR|SRL|SUPPRESS|CVF
    return f"{rxcui}|{lat}||||||1|1|{rxcui}||{sab}|{tty}|{rxcui}|{name}||N|4096|"


CONSO = [
    _row(100, "ENG", "RXNORM", "IN", "alphadrug"),
    _row(200, "ENG", "RXNORM", "IN", "betadrug"),
    _row(201, "ENG", "RXNORM", "IN", "betadrug"),                      # same name, two RxCUIs -> ambiguous
    _row(300, "ENG", "RXNORM", "PIN", "gammadrug hydrochloride"),
    _row(400, "ENG", "RXNORM", "BN", "brandname"),                     # brand: never used
    _row(500, "ENG", "RXNORM", "MIN", "alphadrug / betadrug"),         # combination: never used
    _row(600, "SPA", "RXNORM", "IN", "deltadrug"),                     # not English
    _row(700, "ENG", "MTHSPL", "IN", "epsilondrug"),                   # not the RxNorm vocabulary
]


def _drug(i, name):
    n = normalize_name(name)
    return {"ddinter_id": i, "ingredient_normalized": n, "name_normalized": n}


def _rxnorm_file(tmp_path, as_zip):
    text = "\n".join(CONSO) + "\n"
    if as_zip:
        p = tmp_path / "RxNorm_test.zip"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("rrf/RXNCONSO.RRF", text)
        return p
    p = tmp_path / "RXNCONSO.RRF"
    p.write_text(text, encoding="utf-8")
    return p


def test_rxnorm_uses_only_english_ingredient_concepts(tmp_path):
    for as_zip in (False, True):
        idx = read_rxnorm_ingredients(_rxnorm_file(tmp_path, as_zip))
        assert "brandname" not in idx and "deltadrug" not in idx and "epsilondrug" not in idx
        assert not any("/" in k for k in idx)


def test_rxcui_exact_match_and_ambiguity_is_skipped_not_resolved(tmp_path):
    idx = read_rxnorm_ingredients(_rxnorm_file(tmp_path, True))
    drugs = [_drug("D1", "Alphadrug"), _drug("D2", "Betadrug"), _drug("D3", "Alphadrg"), _drug("D4", "Gammadrug hydrochloride")]
    matched, stats = match_rxnorm(drugs, idx)
    assert matched["D1"] == ("100", "IN")
    assert "D2" not in matched and stats["rxcui_ambiguous_skipped"] == 1   # two RxCUIs for one name: no guess
    assert "D3" not in matched                                            # near-miss spelling: no fuzzy match
    assert matched["D4"][1] == "PIN"


def _clinpgx(tmp_path, rows):
    head = "PharmGKB Accession Id\tName\tGeneric Names\tType\tRxNorm Identifiers\tATC Identifiers"
    p = tmp_path / "drugs.tsv"
    p.write_text("\n".join([head] + ["\t".join(r) for r in rows]) + "\n", encoding="utf-8")
    return read_clinpgx_drugs(p)


def test_clinpgx_fills_only_missing_values_from_a_unique_single_drug_record(tmp_path):
    rows = _clinpgx(tmp_path, [
        ["PA1", "alphadrug", "", "Drug", "111", "N02BE01"],
        ["PA2", "classy", "", "Drug Class", "", "N02"],                       # classes are ignored
        ["PA3", "twin", "", "Drug", "5", "A01AB02"], ["PA4", "twin", "", "Drug", "6", "A01AB03"],  # ambiguous name
        ["PA5", "multi", "", "Drug", "7,8", "C01AA05,C01AA05,A01"],           # 2 RxCUIs -> none; 7-char ATC only
    ])
    assert "classy" not in build_clinpgx_index(rows)
    drugs = [_drug("D1", "alphadrug"), _drug("D2", "classy"), _drug("D3", "twin"), _drug("D4", "multi"), _drug("D5", "unknown")]
    rx, atc, stats = clinpgx_topup(drugs, rows, have_rxcui={"D1"}, have_atc=set())
    assert "D1" not in rx and atc["D1"] == ["N02BE01"]                        # already had an RxCUI: not overwritten
    assert "D2" not in atc and "D3" not in rx and "D3" not in atc and stats["clinpgx_name_ambiguous_skipped"] == 1
    assert "D4" not in rx and atc["D4"] == ["C01AA05"]
    assert "D5" not in rx and "D5" not in atc
    rx2, atc2, _ = clinpgx_topup(drugs, rows, have_rxcui=set(), have_atc={"D1"})
    assert rx2["D1"] == "111" and "D1" not in atc2                            # WHO ATC is never overwritten
