"""Tests for the deterministic alternative-drug ranker (agents/resolution/alternatives.py).

Synthetic tests use FICTIONAL drug names and fictional ATC-style codes (leading letter Z, which WHO does not use),
so no real ATC data is copied into this repository. Real-data tests read the existing clean CSVs at runtime and
assert the SAFETY RULES; they never hard-code which drug is the "correct" alternative.

These tests never read or write aegis_rx.db*: only in-memory objects and the clean CSVs are used.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from pydantic import ValidationError

from agents.resolution.alternatives import (
    RANKER_CONFIG,
    ActionPlan,
    AlternativeCandidate,
    AlternativeRanker,
    AlternativeReport,
    DDInterIndex,
    _sort_key,
    assess_risk_tier,
)
from agents.resolution.resolver import SafetyResolutionEngine
from agents.risk.schemas import Finding

REPO_ROOT = Path(__file__).resolve().parents[1]
CLEAN_DIR = REPO_ROOT / "datasets" / "clean"
HAVE_REAL_DATA = (CLEAN_DIR / "ddinter_drugs_clean.csv").exists() and (CLEAN_DIR / "ddinter_pairs_clean.csv").exists()
needs_real_data = pytest.mark.skipif(not HAVE_REAL_DATA, reason="clean DDInter CSVs not present")

ATC_LIKE = re.compile(r"\b[A-Z]\d{2}[A-Z]{2}(\d{2})?\b")
DOSE_LIKE = re.compile(r"\b\d+(\.\d+)?\s?(mg|mcg|ug|g|ml|iu|units?)\b", re.IGNORECASE)
DOSE_KEYS = {"dose", "dose_unit", "frequency", "route"}


# ------------------------------------------------------------------------------------------------------------
# synthetic world builder
# ------------------------------------------------------------------------------------------------------------
class World:
    def __init__(self) -> None:
        self.drugs: List[Dict[str, str]] = []
        self.pairs: List[Dict[str, str]] = []
        self.labels: List[Dict[str, str]] = []
        self._ids: Dict[str, str] = {}

    def drug(self, name: str, atc: str = "", rxcui: str = "", original: Optional[str] = None,
             label: Optional[Dict[str, str]] = None) -> str:
        ddid = f"DDInter{len(self.drugs) + 1}"
        self._ids[name] = ddid
        self.drugs.append({
            "ddinter_id": ddid, "name_original": original or name.title(), "name_base": original or name.title(),
            "qualifier": "", "name_normalized": name, "ingredient_normalized": name, "rxcui": rxcui, "atc_codes": atc})
        if label is not None:
            self.labels.append({"name_normalized": name, "ingestible": "1", "renal_flag": label.get("renal", "0"),
                                "hepatic_flag": label.get("hepatic", "0"),
                                "sections_present": "dosage_and_administration"})
        return ddid

    def pair(self, a: str, b: str, severity: Optional[str] = None) -> None:
        """severity None -> DDInter lists the pair but labels it Unknown. Not calling pair() = absent from DDInter."""
        lo, hi = sorted((self._ids[a], self._ids[b]), key=lambda s: int(s.replace("DDInter", "")))
        self.pairs.append({"id_lo": lo, "id_hi": hi, "severity_label": severity or "",
                           "label_status": "labeled" if severity else "unknown", "evidence_id": f"{lo} | {hi}"})

    def index(self, reverse: bool = False) -> DDInterIndex:
        drugs, pairs = list(self.drugs), list(self.pairs)
        if reverse:
            drugs, pairs = drugs[::-1], pairs[::-1]
        return DDInterIndex(drugs, pairs, self.labels)

    def ranker(self, advisor: Any = None, reverse: bool = False) -> AlternativeRanker:
        return AlternativeRanker(index=self.index(reverse), advisor=advisor)


L4_DEFAULT = {"zorlin": "minor", "zortan": "moderate", "zorbin": "absent", "zorlisted": "listed", "zorcan": "major"}
L3_DEFAULT = {"zerlin": "minor", "zerton": "absent"}
_SEV = {"minor": "Minor", "moderate": "Moderate", "major": "Major"}


def make_world(l4: Optional[Dict[str, str]] = None, l3: Optional[Dict[str, str]] = None,
               target_partner: Optional[str] = None) -> World:
    """flagged drug 'zorvex' (class Z01AA), partner 'partnerx'; siblings with a chosen status against the partner."""
    w = World()
    w.drug("zorvex", "Z01AA01", rxcui="9001")
    w.drug("partnerx", "Z09ZZ01")
    w.drug("zorvexalt", "Z01AA07", rxcui="9001")                 # same RxCUI as zorvex => identical drug
    w.drug("zorcombo", "Z01AA06", original="Zorcombo and Zorplus")  # combination product
    w.drug("zorpatient", "Z01AA09")                              # a same-class drug the patient already takes
    for i, (name, status) in enumerate((L4_DEFAULT if l4 is None else l4).items()):
        w.drug(name, f"Z01AA{10 + i:02d}")                      # level-4 class Z01AA
        _add_status(w, name, "partnerx", status)
    for i, (name, status) in enumerate((L3_DEFAULT if l3 is None else l3).items()):
        w.drug(name, f"Z01A{chr(66 + i)}01")                    # level-3 class Z01A, different level-4 class
        _add_status(w, name, "partnerx", status)
    if target_partner:
        w.pair("zorvex", "partnerx", target_partner)
    return w


def _add_status(w: World, a: str, b: str, status: str) -> None:
    if status in _SEV:
        w.pair(a, b, _SEV[status])
    elif status == "listed":
        w.pair(a, b, None)
    # "absent": nothing added


def names(rs) -> List[str]:
    return [c.drug_name for c in rs.candidates]


def rank_zorvex(w: World, actives=("zorvex", "partnerx"), advisor=None, **kw):
    return w.ranker(advisor).rank_alternatives("zorvex", list(actives), **kw)


def finding(severity="review_required", drug_a="zorvex", drug_b="partnerx", **extra) -> Dict[str, Any]:
    inputs = {"drug_a": drug_a, **({"drug_b": drug_b} if drug_b else {})}
    inputs.update(extra.pop("inputs", {}))
    return {"id": "F-1", "rule_id": "R-1", "severity": severity, "inputs": inputs,
            "trace": {"matched_pair": [d for d in (drug_a, drug_b) if d]}, "source": "TEST_SOURCE",
            "action": extra.pop("action", "review/hold/monitor; evaluate safer alternative"), **extra}


class FakeAdvisor:
    """Stands in for ml.advisory.SeverityAdvisor: {display name (lower): (predicted, confidence, band)}."""

    def __init__(self, table: Dict[str, tuple]):
        self.table, self.calls = table, []

    def advise_pair(self, a: str, b: str):
        self.calls.append((a, b))
        spec = self.table.get(a.lower())
        if spec is None:
            return None
        pred, conf, band = spec
        rest = (1.0 - conf) / 2
        probs = {k: (conf if k == pred else rest) for k in ("Minor", "Moderate", "Major")}
        return SimpleNamespace(predicted_severity=pred, probabilities=probs,
                               reliability=SimpleNamespace(confidence_band=band, regime="both_seen"),
                               model=SimpleNamespace(name="fake-severity-model"))


# ------------------------------------------------------------------------------------------------------------
# candidate generation
# ------------------------------------------------------------------------------------------------------------
def test_level4_candidates_used_and_level3_not_added_when_enough_free_of_major():
    rs = rank_zorvex(make_world())  # 4 level-4 candidates are free of a known Major (>= 3)
    assert not rs.level3_fallback_used
    assert {c.equivalence_level for c in rs.candidates} == {4}
    assert "zerlin" not in [n.lower() for n in names(rs)]


def test_class_fallback_level4_to_level3_when_fewer_than_three_free_of_major():
    w = make_world(l4={"zorlin": "minor", "zorcan": "major", "zorfour": "major"})
    rs = rank_zorvex(w)
    assert rs.level3_fallback_used
    levels = [c.equivalence_level for c in rs.candidates if not c.known_major_interaction]
    assert levels == sorted(levels, reverse=True)  # level 4 always before level 3
    assert {c.equivalence_level for c in rs.candidates} == {3, 4}
    assert all(c.equivalence_label.startswith("ATC level") for c in rs.candidates)
    l3 = [c for c in rs.candidates if c.equivalence_level == 3]
    assert l3 and all(c.provenance.atc_class_used == "Z01A" for c in l3)
    assert all(c.provenance.atc_class_used == "Z01AA" for c in rs.candidates if c.equivalence_level == 4)
    assert any("looser level-3" in n for n in rs.notes)


def test_level4_always_ranks_above_level3_even_against_a_better_interaction():
    w = make_world(l4={"zorlin": "absent", "zorbin": "major", "zorcan": "major"}, l3={"zerlin": "minor"})
    rs = rank_zorvex(w, actives=("zorvex", "partnerx", "zorpatient"))
    order = [(c.drug_name.lower(), c.equivalence_level, c.known_major_interaction) for c in rs.candidates]
    assert order[0][:2] == ("zorlin", 4)          # level-4 unknown ranks above level-3 known Minor
    assert order[1][:2] == ("zerlin", 3)
    assert order[-1][2] and order[-2][2]          # known Major last, whatever the level


def test_active_drugs_flagged_drug_identical_rxcui_and_combinations_are_excluded():
    rs = rank_zorvex(make_world(), actives=("zorvex", "partnerx", "zorpatient"))
    got = {n.lower() for n in names(rs)}
    assert "zorpatient" not in got      # already taking it
    assert "zorvex" not in got          # the flagged drug itself
    assert "zorvexalt" not in got       # same RxCUI as the flagged drug (identical drug)
    assert "zorcombo" not in got and "zorcombo and zorplus" not in got   # combination product
    assert "partnerx" not in got
    # unknown drug names are matched exactly, never fuzzily
    assert make_world().ranker().rank_alternatives("zorvexx", ["partnerx"]).resolved_in_ddinter is False


def test_flagged_drug_missing_or_without_atc_gives_reason_not_candidates():
    w = make_world()
    w.drug("zornoatc", "")
    r = w.ranker()
    miss = r.rank_alternatives("nosuchdrug", ["partnerx"])
    assert miss.candidates == [] and "not found" in miss.unavailable_reason
    noatc = r.rank_alternatives("zornoatc", ["partnerx"])
    assert noatc.candidates == [] and "no ATC class" in noatc.unavailable_reason


def test_missing_clean_data_returns_no_alternatives_with_reason(tmp_path):
    rs = AlternativeRanker(clean_dir=tmp_path, advisor=None).rank_alternatives("zorvex", ["partnerx"])
    assert rs.candidates == [] and "not available" in rs.unavailable_reason


def test_multiple_atc_classes_add_a_route_form_warning():
    w = make_world()
    w.drug("zordual", "Z01AA20;Z02BB01")
    # make zordual share one of the flagged drug's classes and the flagged drug sit in two classes
    w.drugs[0]["atc_codes"] = "Z01AA01;Z02BB02"
    rs = rank_zorvex(w)
    zd = [c for c in rs.candidates if c.drug_name.lower() == "zordual"][0]
    assert any("route/form or indication" in n for n in zd.notes)


# ------------------------------------------------------------------------------------------------------------
# interaction handling: unknown vs known, every active drug, variants
# ------------------------------------------------------------------------------------------------------------
def test_unknown_vs_known_ordering_minor_moderate_unknown_then_known_major():
    rs = rank_zorvex(make_world())
    order = [c.drug_name.lower() for c in rs.candidates if c.equivalence_level == 4]
    assert order[:2] == ["zorlin", "zortan"]                 # known Minor, then known Moderate
    assert set(order[2:4]) == {"zorbin", "zorlisted"}        # unknown (absent / listed Unknown) after the known ones
    assert order[-1] == "zorcan"                             # known Major last
    by = {c.drug_name.lower(): c for c in rs.candidates}
    assert by["zorcan"].known_major_interaction and by["zorcan"].needs_pharmacist_review
    assert not by["zorlin"].known_major_interaction


def test_unknown_pairs_are_shown_as_unknown_never_safe_and_never_better_than_known_minor():
    rs = rank_zorvex(make_world())
    by = {c.drug_name.lower(): c for c in rs.candidates}
    for name, kind in (("zorbin", "absent_from_ddinter"), ("zorlisted", "listed_unknown_in_ddinter")):
        p = by[name].interactions[0]
        assert (p.status, p.severity, p.unknown_kind) == ("unknown", None, kind)
        assert p.effective_class == "unknown" and p.penalty == RANKER_CONFIG["pair_penalty"]["unknown"]
        assert by[name].score < by["zorlin"].score           # never scored better than a known Minor
        assert by[name].rank > by["zorlin"].rank
    assert by["zorlin"].interactions[0].severity == "Minor"
    assert "absent does not mean safe" in by["zorbin"].interactions[0].note.lower()


def test_every_other_active_drug_is_checked_not_only_the_partner():
    w = make_world()
    w.drug("thirdx", "Z09ZZ02")
    w.pair("zorlin", "thirdx", "Major")          # zorlin is Minor with the partner but Major with a third drug
    rs = rank_zorvex(w, actives=("zorvex", "partnerx", "thirdx"))
    zorlin = [c for c in rs.candidates if c.drug_name.lower() == "zorlin"][0]
    assert sorted(p.active_drug for p in zorlin.interactions) == ["partnerx", "thirdx"]
    assert zorlin.known_major_interaction and zorlin.needs_pharmacist_review
    assert rs.candidates[-1].known_major_interaction and rs.candidates[-2].known_major_interaction   # Major vs ANY active drug sorts last


def test_active_drug_not_in_ddinter_is_unknown_not_safe():
    rs = rank_zorvex(make_world(), actives=("zorvex", "partnerx", "mysterydrug"))
    for c in rs.candidates:
        p = [x for x in c.interactions if x.active_drug == "mysterydrug"][0]
        assert p.status == "unknown" and p.unknown_kind == "drug_not_in_ddinter" and p.severity is None


def test_no_active_partners_means_interaction_not_assessed_scored_as_unknown():
    rs = rank_zorvex(make_world(), actives=("zorvex",))
    for c in rs.candidates:
        assert c.interactions == [] and c.interaction_class == "unknown"
        comp = {x.name: x for x in c.components}["interaction_risk"]
        assert comp.score == 100 - RANKER_CONFIG["pair_penalty"]["unknown"]
        assert "NOT assessed" in comp.detail


def test_route_form_variants_use_worst_known_severity():
    w = World()
    w.drug("zorvex", "Z01AA01")
    w.drug("partnerx", "Z09ZZ01")
    w.drug("zorvar", "Z01AA02", rxcui="777")
    w.drug("zorvarsalt", "Z01AA02", rxcui="777")        # a variant of the same ingredient
    w.pair("zorvar", "partnerx", "Minor")
    w.pair("zorvarsalt", "partnerx", "Major")
    rs = rank_zorvex(w)
    c = [x for x in rs.candidates if x.drug_name.lower().startswith("zorvar")]
    assert len(c) == 1 and c[0].known_major_interaction
    assert "differing severities" in c[0].interactions[0].note
    assert any("variants share this ingredient" in n for n in c[0].notes)


# ------------------------------------------------------------------------------------------------------------
# scoring is transparent and deterministic
# ------------------------------------------------------------------------------------------------------------
def test_weights_are_in_one_constant_and_sum_to_one():
    w = RANKER_CONFIG["weights"]
    assert abs(sum(w.values()) - 1.0) < 1e-9
    with pytest.raises(TypeError):
        RANKER_CONFIG["weights"]["interaction"] = 1.0  # type: ignore[index]


def test_score_components_add_up_exactly():
    # known Moderate vs the only partner, level 4, no label: 0.60*65 + 0.25*100 + 0.15*0
    rs = rank_zorvex(make_world(l4={"zortan": "moderate"}, l3={}))
    c = rs.candidates[0]
    comps = {x.name: x for x in c.components}
    assert comps["interaction_risk"].score == 65.0
    assert comps["clinical_equivalence"].score == 100.0
    assert comps["dosing_evidence"].score == 0.0
    assert c.score == round(sum(x.weighted_points for x in c.components), 2) == 64.0
    assert {x.name for x in c.components} == {"interaction_risk", "clinical_equivalence", "dosing_evidence"}
    assert all(x.detail for x in c.components) and "suggestion for clinician review" in c.reason.lower()


def test_deterministic_tie_break_by_name_and_independent_of_input_order():
    tied = {"zorfox": "absent", "zorbee": "absent", "zorapple": "absent", "zorcat": "absent"}
    a = rank_zorvex(make_world(l4=tied, l3={}), actives=("zorvex", "partnerx", "zorpatient"))
    b = make_world(l4=tied, l3={}).ranker(reverse=True).rank_alternatives("zorvex", ["zorvex", "partnerx", "zorpatient"])
    assert names(a) == sorted(names(a), key=str.lower) == ["Zorapple", "Zorbee", "Zorcat", "Zorfox"]
    assert a.model_dump() == b.model_dump()            # reversed table order => identical result
    assert a.model_dump() == rank_zorvex(make_world(l4=tied, l3={}), actives=("zorvex", "partnerx", "zorpatient")).model_dump()
    assert [c.rank for c in a.candidates] == [1, 2, 3, 4]


def test_results_are_capped_and_report_how_many_were_not_shown():
    big = {f"zorx{chr(97 + i)}{chr(97 + j)}": "absent" for i in range(3) for j in range(5)}  # 15 candidates
    rs = rank_zorvex(make_world(l4=big, l3={}), actives=("zorvex", "partnerx", "zorpatient"))
    assert len(rs.candidates) == RANKER_CONFIG["max_alternatives"] and rs.n_considered == 15 and rs.n_not_shown == 5


# ------------------------------------------------------------------------------------------------------------
# dosing evidence: never invents a dose
# ------------------------------------------------------------------------------------------------------------
def test_dosing_unavailable_without_label_and_no_dose_is_ever_proposed():
    w = make_world()
    rs = rank_zorvex(w)
    for c in rs.candidates:
        assert c.dosing.status == "unavailable" and not c.dosing.label_found and c.dosing.dose_proposed is False
        assert "dosing data unavailable" in c.dosing.note.lower()
    blob = json.dumps(rs.model_dump())
    assert not DOSE_LIKE.search(blob)
    assert not (DOSE_KEYS & _all_keys(rs.model_dump()))


def test_dosing_uses_label_flags_only_as_evidence_tiers():
    w = make_world(l4={"zorlin": "minor", "zortan": "minor", "zorbin": "minor"}, l3={})
    w.labels += [
        {"name_normalized": "zorlin", "ingestible": "1", "renal_flag": "1", "hepatic_flag": "0", "sections_present": "dosage_and_administration"},
        {"name_normalized": "zortan", "ingestible": "1", "renal_flag": "0", "hepatic_flag": "0", "sections_present": "contraindications"},
        {"name_normalized": "zorbin", "ingestible": "0", "renal_flag": "1", "hepatic_flag": "1", "sections_present": ""},  # not ingestible
    ]
    by_ctx = {}
    for ctx in (None, {"renal_context": "declining"}):
        rs = w.ranker().rank_alternatives("zorvex", ["zorvex", "partnerx"], patient_context=ctx)
        by_ctx[bool(ctx)] = {c.drug_name.lower(): c.dosing for c in rs.candidates}
    assert by_ctx[False]["zorlin"].status == "label_flags_context_not_given"
    assert by_ctx[True]["zorlin"].status == "label_flags_match_context" and by_ctx[True]["zorlin"].patient_context_used == ["renal"]
    assert by_ctx[True]["zortan"].status == "label_only" and "not evidence" in by_ctx[True]["zortan"].note
    assert by_ctx[True]["zorbin"].status == "unavailable"          # non-ingestible label row is ignored
    for d in by_ctx[True].values():
        assert d.dose_proposed is False and "no dose is proposed" in d.note.lower()


def _all_keys(obj: Any) -> set:
    keys: set = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            keys |= _all_keys(v)
    return keys


# ------------------------------------------------------------------------------------------------------------
# human co-sign is never optional
# ------------------------------------------------------------------------------------------------------------
def test_requires_cosign_always_true_and_cannot_be_disabled():
    w = make_world(target_partner="Major")
    report = w.ranker().build_report(finding())
    assert report.requires_cosign is True and report.auto_execute is False
    cands = [c for s in report.replacement_sets for c in s.candidates]
    assert cands and all(c.requires_cosign and c.is_suggestion and not c.auto_execute for c in cands)
    assert all("clinician review" in c.disclaimer.lower() for c in cands) and "clinician review" in report.disclaimer.lower()
    dump = cands[0].model_dump()
    for bad in ({"requires_cosign": False}, {"auto_execute": True}):
        with pytest.raises(ValidationError):
            AlternativeCandidate(**{**dump, **bad})
    with pytest.raises(ValidationError):
        AlternativeReport(risk_tier="HIGH", gate_reason="x", requires_cosign=False)
    with pytest.raises(ValidationError):
        ActionPlan(action_type="monitor", description="d", reason="r", requires_cosign=False)


# ------------------------------------------------------------------------------------------------------------
# A) risk gate
# ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("sev", ["critical", "contraindicated", "major", "review_required", "high", "Major", "REVIEW_REQUIRED"])
def test_high_risk_severities_generate_full_ranked_alternatives(sev):
    rep = make_world().ranker().build_report(finding(sev))
    assert rep.risk_tier == "HIGH"
    assert [s.replaces for s in rep.replacement_sets] == ["zorvex", "partnerx"]
    assert all(s.tier == "full" for s in rep.replacement_sets)
    assert rep.replacement_sets[0].candidates and rep.monitoring_action is None


def test_ddinter_known_major_for_the_flagged_pair_raises_a_low_finding_to_high():
    w = make_world(target_partner="Major")
    rep = w.ranker().build_report(finding("low"))
    assert rep.risk_tier == "HIGH" and "known Major" in rep.gate_reason and rep.replacement_sets
    # the same low finding without a DDInter Major stays LOW
    assert make_world(target_partner="Moderate").ranker().build_report(finding("low")).risk_tier == "LOW"


def test_lab_triggered_high_severity_finding_generates_alternatives():
    f = finding("review_required", drug_a="zorvex", drug_b=None, inputs={"renal_context": "declining", "latest_renal_lab": 2.4})
    f["trace"] = {"matched_pair": ["zorvex"]}
    rep = make_world().ranker().build_report(f, active_medications=["zorvex", "partnerx"])
    assert rep.risk_tier == "HIGH" and [s.replaces for s in rep.replacement_sets] == ["zorvex"]  # only the flagged drug
    assert rep.replacement_sets[0].candidates and rep.monitoring_action is None


def test_moderate_gives_monitoring_action_and_a_short_secondary_consider_list():
    rep = make_world().ranker().build_report(finding("moderate", action="Monitor INR twice weekly per rule"))
    assert rep.risk_tier == "MODERATE"
    assert rep.monitoring_action.action_type == "monitor" and "Monitor INR" in rep.monitoring_action.description
    assert rep.monitoring_action.requires_cosign and rep.monitoring_action.dose_proposed is False
    for s in rep.replacement_sets:
        assert s.tier == "consider" and len(s.candidates) <= RANKER_CONFIG["max_consider"] == 3
        assert not any(c.known_major_interaction for c in s.candidates)
        assert any("secondary" in n for n in s.notes)
    assert [c.rank for c in rep.replacement_sets[0].candidates] == [1, 2, 3]


def test_moderate_without_validated_action_does_not_invent_monitoring():
    rep = make_world().ranker().build_report(finding("moderate", action=None))
    assert "no validated monitoring protocol" in rep.monitoring_action.description


@pytest.mark.parametrize("sev", ["low", "minor", "undetermined", "unspecified", "", "something odd"])
def test_low_and_unknown_severity_get_no_alternatives_and_a_reason(sev):
    rep = make_world().ranker().build_report(finding(sev))
    assert rep.risk_tier in {"LOW", "UNKNOWN"} and rep.replacement_sets == []
    assert rep.no_alternatives_reason and rep.gate_reason
    assert (rep.risk_tier == "LOW") == (sev in {"low", "minor"})


def test_assess_risk_tier_is_pure_and_deterministic():
    assert assess_risk_tier("critical")[0] == "HIGH"
    assert assess_risk_tier("moderate")[0] == "MODERATE"
    assert assess_risk_tier("moderate", ddinter_known_major=True)[0] == "HIGH"
    assert assess_risk_tier(None)[0] == "UNKNOWN"


def test_pair_finding_ranks_each_drug_separately_labelled_replaces():
    rep = make_world().ranker().build_report(finding())
    assert [s.replaces for s in rep.replacement_sets] == ["zorvex", "partnerx"]
    first = rep.replacement_sets[0]
    assert first.candidates and all(c.replaces == "zorvex" for c in first.candidates)
    assert rep.replacement_sets[1].candidates == [] and rep.replacement_sets[1].unavailable_reason  # no class siblings


# ------------------------------------------------------------------------------------------------------------
# duplicate therapy => consolidation, not substitution
# ------------------------------------------------------------------------------------------------------------
def test_duplicate_therapy_returns_consolidation_action_and_no_alternatives():
    f = finding("review_required", drug_a="zorlin", drug_b="zortan",
                inputs={"duplicate_class": "ace_inhibitor", "active_ace_inhibitors": ["zorlin", "zortan"]})
    rep = make_world().ranker().build_report(f, active_medications=["zorlin", "zortan"])
    assert rep.replacement_sets == []
    act = rep.consolidation_action
    assert act.action_type == "consolidate_duplicate_therapy" and act.requires_cosign and act.dose_proposed is False
    assert act.drugs == ["zorlin", "zortan"] and "recreate the duplication" in act.reason
    assert "consolidation" in rep.no_alternatives_reason.lower()
    assert act.rule_id == "R-1" and act.source == "TEST_SOURCE"        # provenance of the original finding is kept


# ------------------------------------------------------------------------------------------------------------
# B) ML advisory: display always, raise caution only
# ------------------------------------------------------------------------------------------------------------
def _by_name(rs) -> Dict[str, AlternativeCandidate]:
    return {c.drug_name.lower(): c for c in rs.candidates}


def test_ml_is_only_called_for_unknown_pairs_never_for_known_ones():
    adv = FakeAdvisor({"zorbin": ("Moderate", 0.9, "high"), "zorlin": ("Major", 0.9, "high")})
    rank_zorvex(make_world(), advisor=adv)
    called = {a.lower() for a, _ in adv.calls}
    assert "zorbin" in called and "zorlisted" in called
    assert not ({"zorlin", "zortan", "zorcan"} & called)       # known DDInter labels are never second-guessed


def test_ml_major_raises_caution_lowers_rank_and_flags_pharmacist_review():
    base = _by_name(rank_zorvex(make_world(l3={})))
    adv = FakeAdvisor({"zorbin": ("Major", 0.8, "high")})
    got = _by_name(rank_zorvex(make_world(l3={}), advisor=adv))
    p = got["zorbin"].interactions[0]
    assert p.status == "unknown" and p.severity is None                  # still unknown, not relabelled
    assert p.effective_class == "ml_major" and p.penalty == RANKER_CONFIG["pair_penalty"]["ml_major"]
    assert p.ml_advisory.predicted_severity == "Major" and p.ml_advisory.effect_on_rank == "raised_caution"
    assert p.ml_advisory.reliable and p.ml_advisory.model_name == "fake-severity-model"
    assert p.ml_advisory.probabilities["Major"] == 0.8 and p.ml_advisory.confidence_band == "high"
    assert got["zorbin"].needs_pharmacist_review and any("ML estimate" in r for r in got["zorbin"].review_reasons)
    assert got["zorbin"].score < base["zorbin"].score
    assert got["zorbin"].rank > got["zorlisted"].rank                    # below the equal, un-flagged unknown
    assert not got["zorbin"].known_major_interaction                     # an estimate is not a DDInter label


@pytest.mark.parametrize("pred,conf,band", [("Minor", 0.95, "high"), ("Moderate", 0.9, "high"), ("Minor", 0.6, "medium")])
def test_ml_minor_or_moderate_never_improves_rank_or_turns_unknown_into_safe(pred, conf, band):
    base = _by_name(rank_zorvex(make_world(l3={})))
    adv = FakeAdvisor({"zorbin": (pred, conf, band), "zorlisted": (pred, conf, band)})
    rs = rank_zorvex(make_world(l3={}), advisor=adv)
    got = _by_name(rs)
    for n in ("zorbin", "zorlisted"):
        p = got[n].interactions[0]
        assert p.status == "unknown" and p.severity is None and p.effective_class == "unknown"
        assert p.penalty == RANKER_CONFIG["pair_penalty"]["unknown"]
        assert p.ml_advisory.predicted_severity == pred and p.ml_advisory.effect_on_rank == "none"
        assert got[n].score == base[n].score and got[n].rank == base[n].rank
        assert not got[n].needs_pharmacist_review
    assert got["zorbin"].rank > got["zorlin"].rank and got["zorbin"].rank > got["zortan"].rank   # still below known Minor/Moderate


def test_low_confidence_ml_estimate_is_shown_as_unreliable_and_changes_nothing():
    base = _by_name(rank_zorvex(make_world(l3={})))
    adv = FakeAdvisor({"zorbin": ("Major", 0.40, "low")})
    got = _by_name(rank_zorvex(make_world(l3={}), advisor=adv))
    p = got["zorbin"].interactions[0]
    assert p.ml_advisory.reliable is False and p.ml_advisory.effect_on_rank == "none"
    assert "unreliable" in p.ml_advisory.note and p.effective_class == "unknown"
    assert got["zorbin"].score == base["zorbin"].score and got["zorbin"].rank == base["zorbin"].rank
    assert not got["zorbin"].needs_pharmacist_review


def test_ml_failures_or_no_estimate_do_not_break_ranking():
    class Boom:
        def advise_pair(self, a, b):
            raise RuntimeError("model exploded")

    plain = rank_zorvex(make_world()).model_dump()
    assert rank_zorvex(make_world(), advisor=Boom()).model_dump() == plain
    assert rank_zorvex(make_world(), advisor=FakeAdvisor({})).model_dump() == plain


def test_ranker_works_when_the_ml_artifact_is_missing_and_loads_it_lazily(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_ML_ARTIFACT", str(tmp_path / "does_not_exist.json"))
    r = make_world().ranker(advisor="auto")
    assert r._advisor_loaded is False                    # nothing loaded at construction
    assert r._advisory_used() is False
    rs = r.rank_alternatives("zorvex", ["zorvex", "partnerx"])
    assert r._advisor_loaded is True and r._advisor is None
    assert rs.candidates and all(p.ml_advisory is None for c in rs.candidates for p in c.interactions)
    assert r.build_report(finding()).ml_advisory_available is False
    assert rs.model_dump() == rank_zorvex(make_world()).model_dump()


def test_ml_artifact_is_not_loaded_for_tiers_that_do_not_rank(monkeypatch):
    calls = []
    monkeypatch.setattr("ml.advisory.get_advisor", lambda: calls.append(1))
    r = make_world().ranker(advisor="auto")
    r.build_report(finding("low"))
    r.build_report(finding("undetermined"))
    assert calls == []


# ------------------------------------------------------------------------------------------------------------
# integration with the resolver (alternative_ranker is optional)
# ------------------------------------------------------------------------------------------------------------
class FakeLedger:
    def __init__(self):
        self.events: List[tuple] = []

    def append_event(self, actor, event_type, payload):
        self.events.append((actor, event_type, payload))


def _engine_finding() -> Finding:
    return Finding(
        rule_id="TEST-RULE-1", severity="review_required", title="Documented Interaction: zorvex + partnerx",
        description="Synthetic interaction used only by tests.", action="review/hold/monitor; evaluate safer alternative",
        inputs={"drug_a": "zorvex", "drug_b": "partnerx", "patient_id": "P-1"},
        trace={"rule_id": "TEST-RULE-1", "evidence_id": "EV-1", "source": "TEST_SOURCE", "source_version": "9.9",
               "matched_pair": ["partnerx", "zorvex"]},
        source="TEST_SOURCE")


def _stable(res) -> dict:
    d = res.model_dump(mode="json", exclude={"alternatives"})
    if d.get("simulated_order"):
        d["simulated_order"].pop("created_at")
    return d


def test_default_resolver_behaviour_is_unchanged_without_a_ranker():
    res = SafetyResolutionEngine().resolve_finding_pipeline(_engine_finding())
    assert res.alternatives is None
    assert "alternatives" not in res.model_dump() and "alternatives" not in res.model_dump(mode="json")
    assert "alternatives" not in json.loads(res.model_dump_json())
    assert SafetyResolutionEngine().alternative_ranker is None


def test_ranker_attaches_alternatives_without_changing_the_original_resolution():
    led_a, led_b = FakeLedger(), FakeLedger()
    base = SafetyResolutionEngine(audit_ledger=led_a).resolve_finding_pipeline(_engine_finding())
    ranker = make_world().ranker()
    res = SafetyResolutionEngine(audit_ledger=led_b, alternative_ranker=ranker).resolve_finding_pipeline(
        _engine_finding(), active_medications=["zorvex", "partnerx"])
    assert _stable(res) == _stable(base)                              # decision, order and provenance untouched
    assert res.rule_id == "TEST-RULE-1" and res.source == "TEST_SOURCE" and res.evidence_id == "EV-1" and res.source_version == "9.9"
    assert res.requires_cosign and res.simulated_order.requires_cosign and res.simulated_order.auto_execute is False
    alt = res.alternatives
    assert alt.risk_tier == "HIGH" and [s.replaces for s in alt.replacement_sets] == ["zorvex", "partnerx"]
    assert alt.replacement_sets[0].candidates and alt.requires_cosign
    assert "alternatives" in res.model_dump()
    # audit ledger: original events identical, one extra event for the suggestions
    assert [e for e in led_b.events if e[1] != "ALTERNATIVES_RANKED"] == led_a.events
    extra = [e for e in led_b.events if e[1] == "ALTERNATIVES_RANKED"]
    assert len(extra) == 1 and extra[0][2]["requires_cosign"] is True and extra[0][2]["auto_execute"] is False
    assert extra[0][2]["rule_id"] == "TEST-RULE-1" and extra[0][2]["evidence_id"] == "EV-1"


def test_a_failing_ranker_never_breaks_the_safety_pipeline():
    class Broken:
        def build_report(self, *a, **k):
            raise RuntimeError("boom")

    base = SafetyResolutionEngine().resolve_finding_pipeline(_engine_finding())
    res = SafetyResolutionEngine(alternative_ranker=Broken()).resolve_finding_pipeline(_engine_finding())
    assert _stable(res) == _stable(base)
    assert res.alternatives.risk_tier == "UNKNOWN" and res.alternatives.replacement_sets == [] and "RuntimeError" in res.alternatives.no_alternatives_reason


def test_batch_pipeline_feeds_existing_medications_to_the_ranker():
    meds = [{"drug_name": "zorvex"}, {"drug_name": "partnerx"}, {"drug_name": "zorpatient"}]
    results = SafetyResolutionEngine(alternative_ranker=make_world().ranker()).resolve_findings_pipeline(
        [_engine_finding()], existing_medications=meds)
    cands = [c.drug_name.lower() for s in results[0].alternatives.replacement_sets for c in s.candidates]
    assert cands and "zorpatient" not in cands and "partnerx" not in cands


# ------------------------------------------------------------------------------------------------------------
# real data + the three proposal demo cases (assert the safety rules, never a "correct" drug)
# ------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def real_ranker() -> AlternativeRanker:
    return AlternativeRanker(advisor=None)


def _norm(n: str) -> str:
    from data_pipeline.names import normalize_name
    return normalize_name(n)


def assert_safety_rules(report: AlternativeReport, ranker: AlternativeRanker, active_names: List[str]) -> int:
    idx = ranker.index
    actives = {_norm(a) for a in active_names}
    assert report.requires_cosign is True and report.auto_execute is False and "clinician review" in report.disclaimer.lower()
    total = 0
    for rs in report.replacement_sets:
        assert rs.candidates == sorted(rs.candidates, key=_sort_key)           # deterministic documented order
        majors = [i for i, c in enumerate(rs.candidates) if c.known_major_interaction]
        assert majors == list(range(len(rs.candidates) - len(majors), len(rs.candidates)))   # known Major always last
        for c in rs.candidates:
            total += 1
            assert c.requires_cosign and c.is_suggestion and not c.auto_execute
            assert _norm(c.drug_name) not in actives and _norm(c.drug_name) != _norm(rs.replaces)   # never an active drug
            assert c.replaces == rs.replaces and c.equivalence_level in (3, 4)
            assert c.provenance.atc_class_used and "DDInter" in c.provenance.interaction_source
            assert c.dosing.dose_proposed is False
            cidx = idx.resolve(c.drug_name)
            assert cidx is not None and not idx.entities[cidx].is_combination
            for p in c.interactions:
                aidx = idx.resolve(p.active_drug)
                rank = idx.severity_rank(cidx, aidx) if aidx is not None else 0
                if p.status == "known":
                    assert rank == {"Minor": 1, "Moderate": 2, "Major": 3}[p.severity]
                else:   # unknown is never presented as safe: no severity, and never scored better than a known Minor
                    assert rank == 0 and p.severity is None and p.effective_class in {"unknown", "ml_major"}
                    assert p.penalty >= RANKER_CONFIG["pair_penalty"]["unknown"] > RANKER_CONFIG["pair_penalty"]["minor"]
            assert c.known_major_interaction == any(p.severity == "Major" for p in c.interactions)
            if c.known_major_interaction:
                assert c.needs_pharmacist_review
        # an unknown candidate never outranks a known Minor/Moderate one at the same level
        for lvl in (4, 3):
            seq = [c for c in rs.candidates if c.equivalence_level == lvl and not c.known_major_interaction]
            kinds = [c.interaction_class for c in seq]
            assert kinds == sorted(kinds, key=lambda k: {"minor": 1, "moderate": 2, "unknown": 3, "ml_major": 4}[k])
    blob = json.dumps(report.model_dump())
    assert not DOSE_LIKE.search(blob) and not (DOSE_KEYS & _all_keys(report.model_dump()))
    return total


@needs_real_data
def test_demo_warfarin_fluconazole(real_ranker, capsys):
    f = finding("review_required", "warfarin", "fluconazole")
    rep = real_ranker.build_report(f, active_medications=["warfarin", "fluconazole"])
    assert rep.risk_tier == "HIGH" and [s.replaces for s in rep.replacement_sets] == ["warfarin", "fluconazole"]
    assert assert_safety_rules(rep, real_ranker, ["warfarin", "fluconazole"]) > 0
    # each side is checked against the other, still-active drug
    assert all(p.active_drug in {"fluconazole"} for c in rep.replacement_sets[0].candidates for p in c.interactions)
    assert all(p.active_drug in {"warfarin"} for c in rep.replacement_sets[1].candidates for p in c.interactions)
    again = AlternativeRanker(advisor=None).build_report(f, active_medications=["warfarin", "fluconazole"])
    assert again.model_dump() == rep.model_dump()
    with capsys.disabled():
        _print_demo("warfarin + fluconazole", rep)


@needs_real_data
def test_demo_enoxaparin_with_declining_renal_function(real_ranker, capsys):
    f = finding("review_required", "enoxaparin", None, inputs={"renal_context": "declining", "latest_renal_lab": 2.1})
    f["trace"] = {"matched_pair": ["enoxaparin"]}
    rep = real_ranker.build_report(f, active_medications=["enoxaparin", "warfarin", "ibuprofen"])
    assert rep.risk_tier == "HIGH" and [s.replaces for s in rep.replacement_sets] == ["enoxaparin"]
    assert assert_safety_rules(rep, real_ranker, ["enoxaparin", "warfarin", "ibuprofen"]) > 0
    for c in rep.replacement_sets[0].candidates:
        assert {p.active_drug for p in c.interactions} == {"warfarin", "ibuprofen"}      # every other active drug
        if not c.dosing.label_found:
            assert c.dosing.status == "unavailable" and "dosing data unavailable" in c.dosing.note.lower()
        elif c.dosing.renal_label_flag:
            assert c.dosing.status == "label_flags_match_context" and c.dosing.patient_context_used == ["renal"]
    with capsys.disabled():
        _print_demo("enoxaparin + declining renal function", rep)


@needs_real_data
def test_demo_duplicate_ace_inhibitor_returns_consolidation_not_substitution(real_ranker, capsys):
    f = finding("review_required", "lisinopril", "enalapril",
                inputs={"duplicate_class": "ace_inhibitor", "active_ace_inhibitors": ["lisinopril", "enalapril"]})
    rep = real_ranker.build_report(f, active_medications=["lisinopril", "enalapril"])
    assert rep.replacement_sets == [] and rep.risk_tier == "HIGH"
    assert rep.consolidation_action.action_type == "consolidate_duplicate_therapy"
    assert set(rep.consolidation_action.drugs) == {"lisinopril", "enalapril"} and rep.consolidation_action.requires_cosign
    assert rep.consolidation_action.dose_proposed is False and rep.no_alternatives_reason
    with capsys.disabled():
        _print_demo("duplicate ACE inhibitor", rep)


@needs_real_data
def test_real_index_sanity(real_ranker):
    idx = real_ranker.index
    assert len(idx.entities) > 1500 and idx.n_pair_rows > 150000
    assert idx.resolve("warfarin") is not None and idx.resolve("warfarinn") is None      # exact names only
    w, f = idx.resolve("warfarin"), idx.resolve("fluconazole")
    assert idx.pair(w, f) is not None                                                    # a documented DDInter pair


def _print_demo(title: str, rep: AlternativeReport) -> None:
    print(f"\n=== DEMO: {title} | tier {rep.risk_tier} ===")
    for s in rep.replacement_sets:
        print(f"  replaces: {s.replaces}  (considered {s.n_considered}, level-3 fallback: {s.level3_fallback_used})")
        for c in s.candidates:
            inter = ", ".join(f"{p.active_drug}={p.severity or 'unknown'}" for p in c.interactions) or "not assessed"
            flag = " [KNOWN MAJOR]" if c.known_major_interaction else ""
            print(f"    {c.rank:>2}. {c.drug_name:<22} L{c.equivalence_level} score {c.score:>5} | {inter} | dosing: {c.dosing.status}{flag}")
    if rep.consolidation_action:
        print(f"  action: {rep.consolidation_action.action_type} for {rep.consolidation_action.drugs}")


# ------------------------------------------------------------------------------------------------------------
# C) coverage script and committed report
# ------------------------------------------------------------------------------------------------------------
def _load_coverage_module():
    spec = importlib.util.spec_from_file_location("alt_cov", REPO_ROOT / "scripts" / "alternative_ranker_coverage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cov_world(statuses: Dict[str, str]) -> World:
    """flagged 'zorvex' <-> 'partnerx' is a DDInter Major pair; siblings of zorvex have the given status vs the partner."""
    w = World()
    w.drug("zorvex", "Z01AA01")
    w.drug("partnerx", "Z09ZZ01")
    for i, (name, status) in enumerate(statuses.items()):
        w.drug(name, f"Z01AA{10 + i:02d}")
        _add_status(w, name, "partnerx", status)
    w.pair("zorvex", "partnerx", "Major")
    return w


def test_coverage_known_lower_category_on_a_synthetic_world():
    cov_mod = _load_coverage_module()
    w = cov_world({"zorlin": "minor"})
    cov = cov_mod.compute_coverage(w.index(), w.pairs)
    assert cov["major_rows"] == 1 and cov["pairs_analysed"] == 1 and cov["sides"] == 2
    # replacing zorvex: a known Minor sibling exists; replacing partnerx: no same-class sibling at all
    assert cov["per_side"]["level4"] == {"known_lower": 1, "unknown_only": 0, "all_major": 0, "none": 1}
    assert cov["per_pair"]["level4"]["known_lower"] == 1 and cov["per_pair"]["level4_or_3"]["known_lower"] == 1


def test_coverage_unknown_only_and_all_major_categories():
    cov_mod = _load_coverage_module()
    w = cov_world({"zorbin": "absent", "zorlisted": "listed"})
    c1 = cov_mod.compute_coverage(w.index(), w.pairs)
    assert c1["per_side"]["level4"]["unknown_only"] == 1             # absent / listed-Unknown are NOT counted as safe
    assert c1["per_side"]["level4"]["known_lower"] == 0
    c2 = cov_mod.compute_coverage(cov_world({"zorcan": "major"}).index(), cov_world({"zorcan": "major"}).pairs)
    assert c2["major_rows"] == 2 and c2["per_side"]["level4"]["all_major"] == 2


def test_coverage_markdown_states_the_limits_and_contains_no_atc_codes():
    cov_mod = _load_coverage_module()
    w = cov_world({"zorlin": "minor"})
    md = cov_mod.render_markdown(cov_mod.compute_coverage(w.index(), w.pairs), {"drugs": "0" * 64, "pairs": "1" * 64}, "datasets/clean")
    assert "absent from DDInter is NOT safe" in md and "ATC class is NOT clinical equivalence" in md
    assert not ATC_LIKE.search(md)


@needs_real_data
def test_committed_coverage_report_is_reproducible_and_has_no_atc_data():
    report = REPO_ROOT / "reports" / "alternative_ranker" / "coverage.md"
    assert report.exists(), "run: python scripts/alternative_ranker_coverage.py"
    text = report.read_text(encoding="utf-8")
    assert "absent from DDInter is NOT safe" in text and "ATC class is NOT clinical equivalence" in text
    assert "26,914" in text and not ATC_LIKE.search(text)
    cov_mod = _load_coverage_module()
    import csv
    with open(CLEAN_DIR / "ddinter_drugs_clean.csv", encoding="utf-8", newline="") as fh:
        drugs = list(csv.DictReader(fh))
    with open(CLEAN_DIR / "ddinter_pairs_clean.csv", encoding="utf-8", newline="") as fh:
        pairs = list(csv.DictReader(fh))
    cov = cov_mod.compute_coverage(DDInterIndex(drugs, pairs), pairs)
    fresh = cov_mod.render_markdown(cov, {"drugs": cov_mod._sha256(CLEAN_DIR / "ddinter_drugs_clean.csv"),
                                          "pairs": cov_mod._sha256(CLEAN_DIR / "ddinter_pairs_clean.csv")}, "datasets/clean")
    assert fresh == text, "coverage.md is stale: re-run scripts/alternative_ranker_coverage.py"
