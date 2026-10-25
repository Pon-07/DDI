"""Deterministic, explainable alternative-drug ranker for the Resolution Agent.

What this module is
    For a drug that a safety finding flags, it proposes *same-ATC-class* replacement candidates, scores each one
    with a transparent weighted sum, and returns every score component plus a plain-language reason so a
    pharmacist can see why one option outranked another.

What this module is NOT
    * It is not clinical advice. ATC class is NOT clinical equivalence, and a drug pair that is absent from
      DDInter is NOT evidence of safety. Every output is a suggestion for clinician review and requires co-sign.
    * It never proposes, computes or infers a dose. The dosing component only reports whether label evidence
      exists (an evidence-availability tier), never a margin.
    * No LLM is involved in any decision. The only learned component is the existing, advisory severity
      estimator (ml/advisory.py), which may only RAISE caution (see `_apply_ml_advisory`).

Data (read at runtime from the existing clean CSVs; nothing is copied or written elsewhere)
    datasets/clean/ddinter_drugs_clean.csv  - drugs, exact normalised names, RxCUI, ATC codes
    datasets/clean/ddinter_pairs_clean.csv  - DDInter pair severities (Minor/Moderate/Major or Unknown)
    datasets/clean/openfda_labels_clean.csv - label presence + renal/hepatic keyword flags (dosing evidence)

Ordering (lexicographic, deterministic)
    1. candidates with a KNOWN Major interaction against any active drug are always last (and flagged);
    2. ATC level 4 before ATC level 3;
    3. worst interaction class: known Minor < known Moderate < unknown < ML-raised caution;
    4. higher weighted score (`RANKER_CONFIG`) ;
    5. drug name, then entity key.
"""
from __future__ import annotations

import csv
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

RANKER_VERSION = "alt-ranker-1.0"
DEFAULT_CLEAN_DIR = Path(__file__).resolve().parents[2] / "datasets" / "clean"

# --------------------------------------------------------------------------------------------------------------
# All tunable numbers live here (and only here).
# --------------------------------------------------------------------------------------------------------------
RANKER_CONFIG: Mapping[str, Any] = MappingProxyType({
    # weighted sum of three 0-100 components
    "weights": MappingProxyType({"interaction": 0.60, "equivalence": 0.25, "dosing_evidence": 0.15}),
    # per-pair penalty; component = 100 - worst - other_pairs_factor * (sum of the other penalties), floored at 0
    "pair_penalty": MappingProxyType({
        "minor": 15.0, "moderate": 35.0,
        "unknown": 50.0,      # absent from DDInter / listed Unknown / drug not in DDInter: never better than a known Minor
        "ml_major": 75.0,     # unknown pair that a non-low-confidence ML estimate calls Major (raises caution only)
        "major": 100.0,
    }),
    "other_pairs_factor": 0.1,
    "equivalence_score": MappingProxyType({4: 100.0, 3: 50.0}),
    # evidence-availability tiers (NOT a safety margin; no dose is derived from these)
    "dosing_score": MappingProxyType({
        "unavailable": 0.0,
        "label_only": 40.0,
        "label_flags_context_not_given": 60.0,
        "label_flags_match_context": 80.0,
    }),
    "level3_fallback_min_free_of_major": 3,   # add ATC level-3 candidates when fewer level-4 candidates are free of known Major
    "max_alternatives": 10,                   # per replaced drug, after ranking
    "max_consider": 3,                        # MODERATE tier: optional secondary list
    "ml_low_confidence_below": 0.5,           # same cut-off as ml.advisory._band ("low" < 0.5)
    "high_severity": frozenset({"critical", "contraindicated", "major", "review_required", "high"}),
    "moderate_severity": frozenset({"moderate"}),
    "low_severity": frozenset({"minor", "low"}),
})

DISCLAIMER = (
    "Alternatives are suggestions for clinician review only, not orders. ATC class is not clinical equivalence; a drug "
    "pair that is absent from DDInter is not evidence of safety; no doses are proposed; a pharmacist or prescriber must "
    "review and co-sign. Nothing here is executed automatically."
)

SEVERITY_RANK = {"Minor": 1, "Moderate": 2, "Major": 3}
_CLASS_RANK = {"minor": 1, "moderate": 2, "unknown": 3, "ml_major": 4, "major": 5}
_COMBINATION_RE = re.compile(r"(\band\b|/|\+|,|\bwith\b)", re.IGNORECASE)
_RENAL_NOT_RELEVANT = {"", "missing", "none", "unknown"}


# --------------------------------------------------------------------------------------------------------------
# Output models
# --------------------------------------------------------------------------------------------------------------
class MLAdvisoryView(BaseModel):
    """Display-only view of an ml.advisory estimate for a (candidate, active drug) pair that DDInter does not label."""
    model_config = ConfigDict(extra="forbid")

    kind: str = "ml_severity_advisory"
    advisory_only: bool = True
    clinically_validated: bool = False
    model_name: Optional[str] = None
    predicted_severity: str
    probabilities: Dict[str, float] = Field(default_factory=dict)
    confidence: Optional[float] = None
    confidence_band: Optional[str] = None
    regime: Optional[str] = None
    reliable: bool = Field(description="False when the confidence band is 'low'; such estimates never change the rank")
    effect_on_rank: str = Field(description="'raised_caution' (lowers rank) or 'none' (display only)")
    note: str


class PairAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_drug: str
    status: str = Field(description="'known' (DDInter label) or 'unknown'")
    severity: Optional[str] = Field(None, description="Minor/Moderate/Major when status is 'known'")
    unknown_kind: Optional[str] = Field(
        None, description="'absent_from_ddinter', 'listed_unknown_in_ddinter' or 'drug_not_in_ddinter'")
    evidence_id: Optional[str] = None
    note: Optional[str] = None
    ml_advisory: Optional[MLAdvisoryView] = None
    penalty: float
    effective_class: str = Field(description="minor|moderate|unknown|ml_major|major (what the ranker used)")


class ComponentScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    score: float = Field(description="0-100")
    weight: float
    weighted_points: float
    detail: str


class DosingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="unavailable|label_only|label_flags_context_not_given|label_flags_match_context")
    label_found: bool
    renal_label_flag: Optional[bool] = None
    hepatic_label_flag: Optional[bool] = None
    patient_context_used: List[str] = Field(default_factory=list)
    dose_proposed: bool = False
    note: str


class AlternativeProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interaction_source: str = "DDInter label (datasets/clean/ddinter_pairs_clean.csv)"
    candidate_source: str = "ATC class membership (datasets/clean/ddinter_drugs_clean.csv)"
    atc_class_used: Optional[str] = Field(None, description="ATC class (level 4 = 5 chars, level 3 = 4 chars) shared with the replaced drug")
    equivalence_level: int
    dosing_source: Optional[str] = None
    ml_model: Optional[str] = None
    ranker_version: str = RANKER_VERSION


class AlternativeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drug_name: str
    replaces: str = Field(description="The flagged drug this candidate would replace")
    rank: int
    equivalence_level: int = Field(description="4 = same ATC chemical subgroup (tighter); 3 = same pharmacological subgroup (looser)")
    equivalence_label: str
    score: float
    components: List[ComponentScore]
    interactions: List[PairAssessment]
    interaction_class: str
    known_major_interaction: bool
    needs_pharmacist_review: bool
    review_reasons: List[str] = Field(default_factory=list)
    dosing: DosingEvidence
    reason: str
    notes: List[str] = Field(default_factory=list)
    provenance: AlternativeProvenance
    requires_cosign: bool = True
    auto_execute: bool = False
    is_suggestion: bool = True
    disclaimer: str = DISCLAIMER

    @field_validator("requires_cosign")
    @classmethod
    def _cosign(cls, v: bool) -> bool:
        if not v:
            raise ValueError("requires_cosign must always be True.")
        return True

    @field_validator("auto_execute")
    @classmethod
    def _no_auto(cls, v: bool) -> bool:
        if v:
            raise ValueError("auto_execute must always be False.")
        return False


class ReplacementSet(BaseModel):
    """Ranked replacements for ONE flagged drug ("replaces: <drug>")."""
    model_config = ConfigDict(extra="forbid")

    replaces: str
    tier: str = Field(description="'full' (HIGH risk) or 'consider' (MODERATE risk: optional, secondary)")
    resolved_in_ddinter: bool
    candidates: List[AlternativeCandidate] = Field(default_factory=list)
    n_considered: int = 0
    n_not_shown: int = 0
    level3_fallback_used: bool = False
    unavailable_reason: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


class ActionPlan(BaseModel):
    """A structured, non-dose action (monitoring, or consolidating duplicate therapy)."""
    model_config = ConfigDict(extra="forbid")

    action_type: str
    description: str
    reason: str
    drugs: List[str] = Field(default_factory=list)
    source: Optional[str] = None
    rule_id: Optional[str] = None
    source_version: Optional[str] = None
    evidence_id: Optional[str] = None
    dose_proposed: bool = False
    requires_cosign: bool = True

    @field_validator("requires_cosign")
    @classmethod
    def _cosign(cls, v: bool) -> bool:
        if not v:
            raise ValueError("requires_cosign must always be True.")
        return True


class AlternativeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ranker_version: str = RANKER_VERSION
    risk_tier: str = Field(description="HIGH | MODERATE | LOW | UNKNOWN")
    gate_reason: str
    replacement_sets: List[ReplacementSet] = Field(default_factory=list)
    monitoring_action: Optional[ActionPlan] = None
    consolidation_action: Optional[ActionPlan] = None
    no_alternatives_reason: Optional[str] = None
    ml_advisory_available: bool = False
    advisory_only: bool = True
    requires_cosign: bool = True
    auto_execute: bool = False
    disclaimer: str = DISCLAIMER

    @field_validator("requires_cosign")
    @classmethod
    def _cosign(cls, v: bool) -> bool:
        if not v:
            raise ValueError("requires_cosign must always be True.")
        return True

    @field_validator("auto_execute")
    @classmethod
    def _no_auto(cls, v: bool) -> bool:
        if v:
            raise ValueError("auto_execute must always be False.")
        return False


# --------------------------------------------------------------------------------------------------------------
# Data index (built from the clean CSVs; synthetic rows are accepted for tests)
# --------------------------------------------------------------------------------------------------------------
def _id_sort_key(ddinter_id: str) -> Tuple[int, str]:
    m = re.search(r"(\d+)$", ddinter_id or "")
    return (int(m.group(1)) if m else 10 ** 9, ddinter_id or "")


def _split_codes(raw: Any) -> Tuple[str, ...]:
    return tuple(sorted({c.strip().upper() for c in str(raw or "").split(";") if len(c.strip()) == 7}))


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class DrugEntity:
    """All DDInter ids that are the same ingredient (shared RxCUI or exact normalised name) - route/form variants."""
    index: int
    display: str
    names: FrozenSet[str]
    ids: Tuple[str, ...]
    atc: Tuple[str, ...]
    is_combination: bool
    n_variants: int

    @property
    def key(self) -> str:
        return f"E{self.index:05d}"


@dataclass(frozen=True)
class PairInfo:
    rank: int                  # 0 = DDInter lists the pair but labels it Unknown; 1..3 = Minor/Moderate/Major
    evidence_id: str
    levels: Tuple[str, ...]    # distinct known levels seen across variants


@dataclass(frozen=True)
class LabelEvidence:
    renal_flag: bool
    hepatic_flag: bool
    has_dosing_section: bool
    n_labels: int


class DDInterIndex:
    """In-memory index over the clean tables. Exact normalised-name matching only (no fuzzy matching)."""

    def __init__(self, drug_rows: Iterable[Mapping[str, str]], pair_rows: Iterable[Mapping[str, str]],
                 label_rows: Iterable[Mapping[str, str]] = ()):
        rows = sorted((dict(r) for r in drug_rows), key=lambda r: _id_sort_key(r.get("ddinter_id", "")))
        n = len(rows)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

        owner: Dict[str, int] = {}
        for i, r in enumerate(rows):
            toks = set()
            for col in ("ingredient_normalized", "name_normalized"):
                v = (r.get(col) or "").strip()
                if v:
                    toks.add("n:" + v)
            rx = (r.get("rxcui") or "").strip()
            if rx:
                toks.add("rx:" + rx)
            for t in sorted(toks):
                if t in owner:
                    union(i, owner[t])
                else:
                    owner[t] = i

        groups: Dict[int, List[int]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)

        self.entities: List[DrugEntity] = []
        self._id_to_entity: Dict[str, int] = {}
        self._name_to_entity: Dict[str, int] = {}
        for ent_index, root in enumerate(sorted(groups)):
            members = [rows[i] for i in groups[root]]
            plain = [m for m in members if not (m.get("qualifier") or "").strip()] or members
            display = (plain[0].get("name_base") or plain[0].get("name_original") or plain[0].get("ingredient_normalized") or "").strip()
            names = frozenset(
                v.strip() for m in members for v in (m.get("ingredient_normalized") or "", m.get("name_normalized") or "") if v.strip())
            atc: Set[str] = set()
            for m in members:
                atc.update(_split_codes(m.get("atc_codes")))
            combo = any(_COMBINATION_RE.search(m.get("name_original") or m.get("name_base") or "") for m in members)
            ent = DrugEntity(index=ent_index, display=display, names=names,
                             ids=tuple(m["ddinter_id"] for m in members), atc=tuple(sorted(atc)),
                             is_combination=combo, n_variants=len(members))
            self.entities.append(ent)
            for m in members:
                self._id_to_entity[m["ddinter_id"]] = ent_index
            for nm in names:
                self._name_to_entity.setdefault(nm, ent_index)

        # pair table aggregated to entity level (worst known severity across variants)
        agg: Dict[Tuple[int, int], List[Any]] = {}
        self.n_pair_rows = 0
        self.n_self_pairs_skipped = 0
        for r in pair_rows:
            a, b = self._id_to_entity.get(r.get("id_lo", "")), self._id_to_entity.get(r.get("id_hi", ""))
            if a is None or b is None:
                continue
            self.n_pair_rows += 1
            if a == b:
                self.n_self_pairs_skipped += 1
                continue
            key = (a, b) if a < b else (b, a)
            sev = (r.get("severity_label") or "").strip()
            rank = SEVERITY_RANK.get(sev, 0) if (r.get("label_status") or "").strip() == "labeled" else 0
            cur = agg.get(key)
            ev = (r.get("evidence_id") or "").strip()
            if cur is None:
                agg[key] = [rank, ev, {sev} if rank else set()]
            else:
                if rank > cur[0]:
                    cur[0], cur[1] = rank, ev
                if rank:
                    cur[2].add(sev)
        self._pairs: Dict[Tuple[int, int], PairInfo] = {
            k: PairInfo(rank=v[0], evidence_id=v[1], levels=tuple(sorted(v[2], key=lambda s: SEVERITY_RANK[s])))
            for k, v in agg.items()}

        # ATC prefix indexes (level 4 = first 5 chars, level 3 = first 4 chars)
        self._atc4: Dict[str, List[int]] = {}
        self._atc3: Dict[str, List[int]] = {}
        for ent in self.entities:
            for code in ent.atc:
                self._atc4.setdefault(code[:5], []).append(ent.index)
                self._atc3.setdefault(code[:4], []).append(ent.index)
        for d in (self._atc4, self._atc3):
            for k in d:
                d[k] = sorted(set(d[k]))

        # label evidence (exact normalised-name match; ingestible labels only)
        labs: Dict[str, List[Mapping[str, str]]] = {}
        for r in label_rows:
            if (r.get("ingestible") or "").strip() != "1":
                continue
            nm = (r.get("name_normalized") or "").strip()
            if nm:
                labs.setdefault(nm, []).append(r)
        self._labels: Dict[str, LabelEvidence] = {
            nm: LabelEvidence(
                renal_flag=any(_truthy(r.get("renal_flag")) for r in rs),
                hepatic_flag=any(_truthy(r.get("hepatic_flag")) for r in rs),
                has_dosing_section=any("dosage_and_administration" in (r.get("sections_present") or "") for r in rs),
                n_labels=len(rs))
            for nm, rs in labs.items()}
        self.has_label_data = bool(self._labels)

    # --- construction --------------------------------------------------------------------------------
    @classmethod
    def from_clean_dir(cls, clean_dir: Path = DEFAULT_CLEAN_DIR) -> "DDInterIndex":
        clean_dir = Path(clean_dir)

        def read(name: str, required: bool = True) -> List[Dict[str, str]]:
            p = clean_dir / name
            if not p.exists():
                if required:
                    raise FileNotFoundError(str(p))
                return []
            with open(p, "r", encoding="utf-8", newline="") as fh:
                return list(csv.DictReader(fh))

        return cls(read("ddinter_drugs_clean.csv"), read("ddinter_pairs_clean.csv"), read("openfda_labels_clean.csv", False))

    # --- lookups -------------------------------------------------------------------------------------
    def resolve(self, name: str) -> Optional[int]:
        """Entity index for an exact normalised-name match, else None. No fuzzy matching."""
        for candidate in _name_variants(name):
            hit = self._name_to_entity.get(candidate)
            if hit is not None:
                return hit
        return None

    def entity_for_id(self, ddinter_id: str) -> Optional[int]:
        return self._id_to_entity.get(ddinter_id)

    def pair(self, i: int, j: int) -> Optional[PairInfo]:
        return self._pairs.get((i, j) if i < j else (j, i))

    def severity_rank(self, i: int, j: int) -> int:
        """1..3 for a known Minor/Moderate/Major, 0 for unknown (listed Unknown or absent). Never means 'safe'."""
        p = self._pairs.get((i, j) if i < j else (j, i))
        return p.rank if p else 0

    def label_for(self, ent: DrugEntity) -> Optional[LabelEvidence]:
        found = [self._labels[n] for n in sorted(ent.names) if n in self._labels]
        if not found:
            return None
        return LabelEvidence(renal_flag=any(f.renal_flag for f in found), hepatic_flag=any(f.hepatic_flag for f in found),
                             has_dosing_section=any(f.has_dosing_section for f in found), n_labels=sum(f.n_labels for f in found))

    def class_members(self, ent: DrugEntity) -> Tuple[Dict[int, str], Dict[int, str]]:
        """(level-4 members, level-3-only members), each as {entity index: shared ATC class used}.
        The class reported is the lexicographically smallest shared prefix, so it is deterministic."""
        l4: Dict[int, str] = {}
        l3: Dict[int, str] = {}
        for code in ent.atc:
            for m in self._atc4.get(code[:5], ()):
                if m != ent.index and (m not in l4 or code[:5] < l4[m]):
                    l4[m] = code[:5]
        for code in ent.atc:
            for m in self._atc3.get(code[:4], ()):
                if m != ent.index and m not in l4 and (m not in l3 or code[:4] < l3[m]):
                    l3[m] = code[:4]
        return l4, l3


def _name_variants(name: str) -> List[str]:
    try:
        from data_pipeline.names import normalize_name, split_qualifier  # lazy: heavy package import
    except ModuleNotFoundError:
        import unicodedata
        from agents.knowledge.normalizer import DrugNormalizer
        _norm = DrugNormalizer()
        _qual_re = re.compile(r"^(?P<base>.*?)\s*\((?P<qual>[^()]*)\)\s*$")
        def split_qualifier(text: str) -> Tuple[str, Optional[str]]:
            clean = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()
            m = _qual_re.match(clean)
            if m and m.group("base").strip():
                return m.group("base").strip(), m.group("qual").strip().lower() or None
            return clean, None
        def normalize_name(text: str) -> str:
            clean = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()
            return _norm.normalize(clean)

    raw = (name or "").strip()
    out: List[str] = []
    for text in (raw, split_qualifier(raw)[0] if raw else ""):
        n = normalize_name(text) if text else ""
        if n and n not in out:
            out.append(n)
    return out


# --------------------------------------------------------------------------------------------------------------
# Risk gate
# --------------------------------------------------------------------------------------------------------------
def assess_risk_tier(finding_severity: Optional[str], ddinter_known_major: bool = False,
                     config: Mapping[str, Any] = RANKER_CONFIG) -> Tuple[str, str]:
    """HIGH | MODERATE | LOW | UNKNOWN, with a plain-language reason. A DDInter known Major raises any tier to HIGH."""
    sev = (finding_severity or "").lower().strip()
    if sev in config["high_severity"]:
        return "HIGH", f"Finding severity '{sev}' is in the high-risk set."
    if ddinter_known_major:
        return "HIGH", "DDInter labels the flagged pair as a known Major interaction."
    if sev in config["moderate_severity"]:
        return "MODERATE", "Finding severity 'moderate': monitoring plan plus an optional, secondary 'consider' list."
    if sev in config["low_severity"]:
        return "LOW", f"Finding severity '{sev}' is low risk: no alternatives are generated."
    return "UNKNOWN", (f"Finding severity '{sev or 'missing'}' is undetermined: alternatives are not generated; "
                       "clinician review is required.")


# --------------------------------------------------------------------------------------------------------------
# Ranker
# --------------------------------------------------------------------------------------------------------------
@dataclass
class _Active:
    name: str
    entity: Optional[int]


_AUTO = "auto"


class AlternativeRanker:
    """Deterministic ranker. `advisor`: "auto" (lazy ml.advisory.get_advisor()), None (disabled), or any object with
    `advise_pair(drug_a, drug_b)`. If the artifact is missing the ranker works unchanged, without ML estimates."""

    def __init__(self, index: Optional[DDInterIndex] = None, clean_dir: Optional[Path] = None,
                 advisor: Any = _AUTO, config: Mapping[str, Any] = RANKER_CONFIG):
        self._index = index
        self._clean_dir = Path(clean_dir) if clean_dir else DEFAULT_CLEAN_DIR
        self._advisor_setting = advisor
        self._advisor: Any = None
        self._advisor_loaded = advisor is not _AUTO
        if advisor is not _AUTO:
            self._advisor = advisor
        self.config = config
        self.load_error: Optional[str] = None
        self._lock = threading.Lock()

    # --- lazy resources ------------------------------------------------------------------------------
    @property
    def index(self) -> Optional[DDInterIndex]:
        with self._lock:
            if self._index is None and self.load_error is None:
                try:
                    self._index = DDInterIndex.from_clean_dir(self._clean_dir)
                except (OSError, KeyError, ValueError) as exc:
                    self.load_error = f"{type(exc).__name__}: {exc}"
            return self._index

    @property
    def advisor(self) -> Any:
        with self._lock:
            if not self._advisor_loaded:
                self._advisor_loaded = True
                try:
                    from ml.advisory import get_advisor  # lazy: numpy + JSON artifact
                    self._advisor = get_advisor()
                except Exception:  # artifact/numpy missing or disabled: ranker must still work
                    self._advisor = None
            return self._advisor

    # --- public API ----------------------------------------------------------------------------------
    def rank_alternatives(self, flagged_drug: str, active_medications: Sequence[Any] = (),
                          patient_context: Optional[Mapping[str, Any]] = None, tier: str = "full") -> ReplacementSet:
        """Ranked same-class replacements for `flagged_drug`, checked against every OTHER active drug."""
        idx = self.index
        if idx is None:
            return ReplacementSet(replaces=flagged_drug, tier=tier, resolved_in_ddinter=False,
                                  unavailable_reason=f"DDInter clean data is not available ({self.load_error}); no alternatives generated.")
        flagged_idx = idx.resolve(flagged_drug)
        if flagged_idx is None:
            return ReplacementSet(replaces=flagged_drug, tier=tier, resolved_in_ddinter=False,
                                  unavailable_reason=(f"'{flagged_drug}' was not found in DDInter by exact normalised name "
                                                      "(no fuzzy matching); no alternatives generated."))
        flagged = idx.entities[flagged_idx]
        if not flagged.atc:
            return ReplacementSet(replaces=flagged_drug, tier=tier, resolved_in_ddinter=True,
                                  unavailable_reason="The flagged drug has no ATC class in the clean data, so no same-class candidates can be generated.")

        actives = self._resolve_actives(idx, active_medications, flagged_idx, flagged_drug)
        active_idx = {a.entity for a in actives if a.entity is not None} | {flagged_idx}
        dctx = _dosing_context(patient_context)
        l4, l3 = idx.class_members(flagged)

        def usable(m: int) -> bool:
            return m not in active_idx and not idx.entities[m].is_combination

        evaluated = [self._evaluate(idx, idx.entities[m], 4, cls, actives, dctx, flagged_drug, flagged) for m, cls in sorted(l4.items()) if usable(m)]
        free = sum(1 for c in evaluated if not c.known_major_interaction)
        fallback = False
        if free < int(self.config["level3_fallback_min_free_of_major"]):
            extra = [self._evaluate(idx, idx.entities[m], 3, cls, actives, dctx, flagged_drug, flagged) for m, cls in sorted(l3.items()) if usable(m)]
            fallback = bool(extra)
            evaluated += extra

        evaluated.sort(key=_sort_key)
        cap = int(self.config["max_alternatives"])
        shown = [c.model_copy(update={"rank": i + 1}) for i, c in enumerate(evaluated[:cap])]
        notes = ["ATC class does not encode route/form or indication; route/form and indication of every candidate are not verified.",
                 "A pair absent from DDInter is 'unknown', never 'safe'."]
        if fallback:
            notes.append("Fewer than %d level-4 candidates were free of a known Major interaction, so looser level-3 candidates were added; level 4 always ranks above level 3."
                         % int(self.config["level3_fallback_min_free_of_major"]))
        reason = None
        if not evaluated:
            reason = "No same-class candidates (ATC level 4 or 3) remain after excluding the flagged drug, active drugs and combination products."
        return ReplacementSet(replaces=flagged_drug, tier=tier, resolved_in_ddinter=True, candidates=shown,
                              n_considered=len(evaluated), n_not_shown=max(0, len(evaluated) - len(shown)),
                              level3_fallback_used=fallback, unavailable_reason=reason, notes=notes)

    def build_report(self, finding: Any, active_medications: Optional[Sequence[Any]] = None,
                     patient_context: Optional[Mapping[str, Any]] = None) -> AlternativeReport:
        """Risk-gated alternatives for one finding. Never raises for ordinary data problems."""
        severity = str(_get(finding, "severity") or "undetermined")
        inputs = _get(finding, "inputs") or {}
        trace = _get(finding, "trace") or {}
        inputs = inputs if isinstance(inputs, dict) else {}
        trace = trace if isinstance(trace, dict) else {}
        prov = {
            "source": _get(finding, "source") or trace.get("source"),
            "rule_id": _get(finding, "rule_id") or trace.get("rule_id"),
            "source_version": _get(finding, "source_version") or trace.get("source_version"),
            "evidence_id": _get(finding, "evidence_id") or trace.get("evidence_id"),
        }
        prov = {k: (str(v) if v else None) for k, v in prov.items()}
        ctx = dict(patient_context or {})
        if inputs.get("renal_context") is not None:
            ctx.setdefault("renal_context", inputs.get("renal_context"))
        actives = _collect_actives(active_medications, inputs)

        # duplicate therapy: replacing a drug with a same-class drug would recreate the duplication
        if any(k in inputs for k in ("duplicate_class", "duplicate_drug", "active_duplicates")):
            dup = [str(d) for d in (inputs.get("active_ace_inhibitors") or inputs.get("active_duplicates")
                                    or [inputs.get("drug_a"), inputs.get("drug_b")]) if d]
            tier, why = assess_risk_tier(severity)
            return AlternativeReport(
                risk_tier=tier, gate_reason=why,
                consolidation_action=ActionPlan(
                    action_type="consolidate_duplicate_therapy",
                    description="Consolidate duplicate therapy: clinician to decide which agent in the class to keep and which to stop.",
                    reason=("Duplicate therapy within one drug class. Substituting another same-class drug would recreate the duplication, "
                            "so no replacement drugs are proposed. No dose or schedule is proposed."),
                    drugs=dup, **prov),
                no_alternatives_reason="Duplicate therapy: the resolution is consolidation, not substitution.")

        flagged = _flagged_drugs(inputs, trace)
        if not flagged:
            tier, why = assess_risk_tier(severity)
            return AlternativeReport(risk_tier=tier, gate_reason=why,
                                     no_alternatives_reason="The finding does not identify a drug that could be replaced.")

        known_major = False
        idx = self.index
        if idx is not None and len(flagged) == 2:
            a, b = idx.resolve(flagged[0]), idx.resolve(flagged[1])
            if a is not None and b is not None and a != b:
                known_major = idx.severity_rank(a, b) == SEVERITY_RANK["Major"]
        tier, why = assess_risk_tier(severity, known_major, self.config)

        if tier == "HIGH":
            sets = [self.rank_alternatives(d, _with_partners(actives, flagged, d), ctx, tier="full") for d in flagged]
            return AlternativeReport(risk_tier=tier, gate_reason=why, replacement_sets=sets,
                                     ml_advisory_available=self._advisory_used())
        if tier == "MODERATE":
            cap = int(self.config["max_consider"])
            sets = []
            for d in flagged:
                s = self.rank_alternatives(d, _with_partners(actives, flagged, d), ctx, tier="consider")
                keep = [c for c in s.candidates if not c.known_major_interaction][:cap]
                keep = [c.model_copy(update={"rank": i + 1}) for i, c in enumerate(keep)]
                sets.append(s.model_copy(update={
                    "candidates": keep, "n_not_shown": max(0, s.n_considered - len(keep)),
                    "notes": s.notes + ["Optional and secondary: monitoring is the primary action at this risk level."]}))
            action = _clean(_get(finding, "action"))
            return AlternativeReport(
                risk_tier=tier, gate_reason=why, replacement_sets=sets, ml_advisory_available=self._advisory_used(),
                monitoring_action=ActionPlan(
                    action_type="monitor",
                    description=action or "Clinician to define monitoring; no validated monitoring protocol is attached to this finding.",
                    reason="Moderate risk: monitoring is the primary action; any alternatives are optional and secondary.",
                    drugs=flagged, **prov))
        return AlternativeReport(risk_tier=tier, gate_reason=why, no_alternatives_reason=why)

    # --- internals -----------------------------------------------------------------------------------
    def _advisory_used(self) -> bool:
        """True only if an advisor was actually loaded (it is loaded lazily, never just to answer this)."""
        return bool(self._advisor_loaded and self._advisor is not None)

    def _resolve_actives(self, idx: DDInterIndex, active_medications: Sequence[Any], flagged_idx: int,
                         flagged_drug: str) -> List[_Active]:
        out: List[_Active] = []
        seen: Set[Any] = set()
        for raw in active_medications or ():
            name = _med_name(raw)
            if not name:
                continue
            ent = idx.resolve(name)
            if ent == flagged_idx:
                continue  # the flagged drug itself is being replaced, not an interaction partner
            key = ("e", ent) if ent is not None else ("n", name.lower().strip())
            if key in seen:
                continue
            seen.add(key)
            out.append(_Active(name=name, entity=ent))
        return sorted(out, key=lambda a: (a.name.lower(), a.name))

    def _evaluate(self, idx: DDInterIndex, cand: DrugEntity, level: int, atc_class: str, actives: List[_Active],
                  dctx: Dict[str, bool], replaces: str, flagged: DrugEntity) -> AlternativeCandidate:
        cfg = self.config
        pen = cfg["pair_penalty"]
        pairs: List[PairAssessment] = []
        advisor = None
        for act in actives:
            info = idx.pair(cand.index, act.entity) if act.entity is not None else None
            if info is not None and info.rank:
                sev = ("Minor", "Moderate", "Major")[info.rank - 1]
                note = (f"DDInter lists differing severities across route/form variants ({', '.join(info.levels)}); the worst is used."
                        if len(info.levels) > 1 else None)
                pairs.append(PairAssessment(active_drug=act.name, status="known", severity=sev, evidence_id=info.evidence_id or None,
                                            note=note, penalty=pen[sev.lower()], effective_class=sev.lower()))
                continue
            if act.entity is None:
                kind, why = "drug_not_in_ddinter", "The active drug is not in DDInter, so this pair could not be checked."
            elif info is not None:
                kind, why = "listed_unknown_in_ddinter", "DDInter lists this pair but labels its severity Unknown."
            else:
                kind, why = "absent_from_ddinter", "This pair is absent from DDInter. Absent does not mean safe."
            advisor = advisor or self.advisor
            ml, effective = self._apply_ml_advisory(advisor, cand, act.name)
            pairs.append(PairAssessment(
                active_drug=act.name, status="unknown", unknown_kind=kind, evidence_id=(info.evidence_id if info else None) or None,
                note=why, ml_advisory=ml, penalty=pen[effective], effective_class=effective))

        # interaction component
        if pairs:
            pens = sorted((p.penalty for p in pairs), reverse=True)
            inter_score = max(0.0, 100.0 - pens[0] - float(cfg["other_pairs_factor"]) * sum(pens[1:]))
            inter_class = max((p.effective_class for p in pairs), key=lambda c: _CLASS_RANK[c])
            inter_detail = "; ".join(f"{p.active_drug}: " + (f"known {p.severity}" if p.status == "known" else
                                     ("unknown (ML-raised caution)" if p.effective_class == "ml_major" else "unknown")) for p in pairs)
        else:
            inter_score = 100.0 - float(pen["unknown"])
            inter_class = "unknown"
            inter_detail = "No other active drugs were supplied, so interaction risk was NOT assessed (scored as unknown, not as safe)."
        known_major = any(p.status == "known" and p.severity == "Major" for p in pairs)
        ml_raised = [p for p in pairs if p.effective_class == "ml_major"]

        equiv_score = float(cfg["equivalence_score"][level])
        dosing = self._dosing(idx, cand, dctx)
        dose_score = float(cfg["dosing_score"][dosing.status])
        w = cfg["weights"]
        comps = [
            ComponentScore(name="interaction_risk", score=round(inter_score, 2), weight=w["interaction"],
                           weighted_points=round(inter_score * w["interaction"], 2), detail=inter_detail),
            ComponentScore(name="clinical_equivalence", score=equiv_score, weight=w["equivalence"],
                           weighted_points=round(equiv_score * w["equivalence"], 2),
                           detail=f"Shares ATC level {level} class {atc_class} with {replaces}. ATC class is not clinical equivalence."),
            ComponentScore(name="dosing_evidence", score=dose_score, weight=w["dosing_evidence"],
                           weighted_points=round(dose_score * w["dosing_evidence"], 2), detail=dosing.note),
        ]
        total = round(sum(c.weighted_points for c in comps), 2)

        reasons: List[str] = []
        if known_major:
            reasons.append("Known Major interaction with an active drug (DDInter): not recommended, listed only so it is visible.")
        if ml_raised:
            reasons.append("An ML estimate (advisory) suggests a possible Major interaction with: " + ", ".join(p.active_drug for p in ml_raised) + ".")
        if any(p.status == "unknown" for p in pairs):
            reasons.append("At least one pair is unknown; treat as unchecked.")
        needs_review = bool(known_major or ml_raised)

        n_known = sum(1 for p in pairs if p.status == "known")
        reason = (f"Same ATC level-{level} class ({atc_class}) as {replaces}. "
                  f"Interactions: {n_known} known, {len(pairs) - n_known} unknown of {len(pairs)} checked. "
                  f"{dosing.note} Suggestion for clinician review only.")
        return AlternativeCandidate(
            drug_name=cand.display, replaces=replaces, rank=0, equivalence_level=level,
            equivalence_label=("ATC level 4 (same chemical subgroup)" if level == 4 else "ATC level 3 (looser: same pharmacological subgroup)"),
            score=total, components=comps, interactions=pairs, interaction_class=inter_class,
            known_major_interaction=known_major, needs_pharmacist_review=needs_review, review_reasons=reasons,
            dosing=dosing, reason=reason,
            notes=_candidate_notes(cand, flagged, replaces),
            provenance=AlternativeProvenance(
                atc_class_used=atc_class, equivalence_level=level,
                dosing_source=("openFDA label flags (datasets/clean/openfda_labels_clean.csv)" if dosing.label_found else None),
                ml_model=next((p.ml_advisory.model_name for p in pairs if p.ml_advisory and p.ml_advisory.model_name), None)))

    def _apply_ml_advisory(self, advisor: Any, cand: DrugEntity, active_name: str) -> Tuple[Optional[MLAdvisoryView], str]:
        """ASYMMETRIC SAFETY RULE: an ML estimate may only RAISE caution.
        * Major with a non-low confidence band -> penalty 'ml_major' (rank lowered) and a pharmacist-review flag.
        * Major with a low band -> shown as unreliable, rank unchanged.
        * Minor/Moderate (any confidence) -> shown only; never improves rank and never turns unknown into safe."""
        if advisor is None:
            return None, "unknown"
        try:
            adv = advisor.advise_pair(cand.display, active_name)
        except Exception:
            return None, "unknown"
        if adv is None:
            return None, "unknown"
        pred = str(getattr(adv, "predicted_severity", "") or "")
        probs = {str(k): float(v) for k, v in (getattr(adv, "probabilities", None) or {}).items()}
        rel = getattr(adv, "reliability", None)
        band = getattr(rel, "confidence_band", None)
        regime = getattr(rel, "regime", None)
        conf = probs.get(pred)
        low = (band == "low") if band else (conf is not None and conf < float(self.config["ml_low_confidence_below"]))
        model = getattr(getattr(adv, "model", None), "name", None)
        raises = pred == "Major" and not low
        if raises:
            note = "ML estimate (advisory only, not clinically validated): possible Major. Lowers rank and flags pharmacist review."
        elif low:
            note = "Low-confidence ML estimate, shown as unreliable. It does not change the rank."
        else:
            note = f"ML estimate '{pred}' is display-only: it never improves rank and never makes an unknown pair safe."
        view = MLAdvisoryView(model_name=model, predicted_severity=pred, probabilities=probs, confidence=conf,
                              confidence_band=band, regime=regime, reliable=not low,
                              effect_on_rank="raised_caution" if raises else "none", note=note)
        return view, ("ml_major" if raises else "unknown")

    def _dosing(self, idx: DDInterIndex, cand: DrugEntity, dctx: Dict[str, bool]) -> DosingEvidence:
        label = idx.label_for(cand)
        if label is None:
            return DosingEvidence(status="unavailable", label_found=False,
                                  note="Dosing data unavailable: no validated label evidence for this drug in the clean data. No dose is proposed.")
        used = [k for k in ("renal", "hepatic") if dctx.get(k)]
        match = (dctx.get("renal") and label.renal_flag) or (dctx.get("hepatic") and label.hepatic_flag)
        if match:
            status = "label_flags_match_context"
            note = ("A label exists and its text is keyword-flagged for renal/hepatic content relevant to this patient context; "
                    "read the label. No dose is proposed.")
        elif label.renal_flag or label.hepatic_flag:
            status = "label_flags_context_not_given"
            note = ("A label exists with renal/hepatic keyword flags; patient renal/hepatic context was not supplied for this check. "
                    "No dose is proposed.")
        else:
            status = "label_only"
            note = ("A label exists, but it has no renal/hepatic keyword flag. That is not evidence that no adjustment is needed. "
                    "No dose is proposed.")
        return DosingEvidence(status=status, label_found=True, renal_label_flag=label.renal_flag, hepatic_label_flag=label.hepatic_flag,
                              patient_context_used=used, note=note)


def _candidate_notes(cand: DrugEntity, flagged: DrugEntity, replaces: str) -> List[str]:
    notes: List[str] = []
    fl4, cl4 = {c[:5] for c in flagged.atc}, {c[:5] for c in cand.atc}
    if len(fl4) > 1:
        notes.append(f"{replaces} belongs to {len(fl4)} ATC level-4 classes and this candidate shares {len(fl4 & cl4)} of them. "
                     "ATC does not encode route/form or indication: verify the candidate fits the intended route and use.")
    if cand.n_variants > 1:
        notes.append(f"{cand.n_variants} DDInter route/form variants share this ingredient; the worst known severity is used.")
    return notes


def _sort_key(c: AlternativeCandidate) -> Tuple[Any, ...]:
    return (c.known_major_interaction, 0 if c.equivalence_level == 4 else 1, _CLASS_RANK[c.interaction_class],
            -c.score, c.drug_name.lower(), c.drug_name)


# --------------------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------------------
def _get(obj: Any, attr: str) -> Any:
    val = obj.get(attr) if isinstance(obj, dict) else getattr(obj, attr, None)
    if attr in ("inputs", "trace") and isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            val = None
    return val


def _clean(val: Any) -> Optional[str]:
    s = str(val).strip() if val is not None else ""
    return s or None


def _med_name(raw: Any) -> Optional[str]:
    if isinstance(raw, dict):
        raw = raw.get("drug_name") or raw.get("name")
    else:
        raw = raw if isinstance(raw, str) else (getattr(raw, "drug_name", None) or getattr(raw, "name", None))
    return _clean(raw)


def _collect_actives(active_medications: Optional[Sequence[Any]], inputs: Mapping[str, Any]) -> List[str]:
    names: List[str] = []
    for raw in list(active_medications or []):
        n = _med_name(raw)
        if n:
            names.append(n)
    for key in ("drug_a", "drug_b"):
        if inputs.get(key):
            names.append(str(inputs[key]))
    for key in ("active_ace_inhibitors", "active_duplicates"):
        names += [str(x) for x in (inputs.get(key) or [])]
    seen: Set[str] = set()
    out: List[str] = []
    for n in names:
        k = n.lower().strip()
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out


def _with_partners(actives: List[str], flagged: List[str], replacing: str) -> List[str]:
    """All active drugs; the ranker drops the replaced drug itself by entity match."""
    return list(actives) + [d for d in flagged if d != replacing]


def _flagged_drugs(inputs: Mapping[str, Any], trace: Mapping[str, Any]) -> List[str]:
    a, b = _clean(inputs.get("drug_a")), _clean(inputs.get("drug_b"))
    if a and b and a.lower() != b.lower():
        return [a, b]
    if a:
        return [a]
    mp = trace.get("matched_pair")
    if isinstance(mp, (list, tuple)):
        names = [str(x).strip() for x in mp if str(x).strip()]
        if len(names) == 2 and names[0].lower() != names[1].lower():
            return names
        if len(names) == 1:
            return names
    return []


def _dosing_context(ctx: Optional[Mapping[str, Any]]) -> Dict[str, bool]:
    ctx = ctx or {}
    renal_ctx = str(ctx.get("renal_context") or "").lower().strip()
    return {
        "renal": bool(ctx.get("renal_impaired")) or renal_ctx not in _RENAL_NOT_RELEVANT,
        "hepatic": bool(ctx.get("hepatic_impaired")),
    }


__all__ = [
    "RANKER_VERSION", "RANKER_CONFIG", "DISCLAIMER", "AlternativeRanker", "AlternativeCandidate", "AlternativeReport",
    "ReplacementSet", "ActionPlan", "MLAdvisoryView", "PairAssessment", "DDInterIndex", "assess_risk_tier",
]
