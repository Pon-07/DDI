"""Explainable, ADVISORY severity estimates, kept strictly separate from rule-based findings.

Design rules
* The rule engine (agents/risk) is untouched. This module never creates, edits, suppresses or re-grades a
  `Finding`; its output is a separate object (`MLSeverityAdvisory`) with `advisory_only=True`.
* It is only offered for interactions that are already documented (a stored rule finding, or a caller that says
  so). It cannot tell whether two drugs interact: the training data has no negative examples.
* Every advisory carries its regime (were the drugs seen in training?), the measured reliability of that kind of
  prediction, an n-gram explanation, the model version and training-data hash, and explicit limitations.
* If the artifact or numpy is unavailable, or AEGIS_ML_ADVISORY=false, advice is simply absent; nothing else changes.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ml.inference import LABELS, SeverityModel

_ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
DEFAULT_ARTIFACT = _ARTIFACT_DIR / "severity_baseline_v2.json"                                   # public, name-only, committed
RESTRICTED_ARTIFACT = _ARTIFACT_DIR / "restricted" / "severity_baseline_v2_atc.json"           # name + WHO ATC, git-ignored

DISCLAIMER = ("Machine-learning ESTIMATE, not a clinical finding. It has not been clinically validated and must not "
              "be used to override, downgrade or replace the rule-based safety findings or a pharmacist's judgement.")

LIMITATIONS = [
    "Trained only on DDInter interacting pairs (all 8 categories, ~130k labelled pairs); it cannot tell whether two drugs interact.",
    "Uses drug-name text (and WHO ATC classes only in the restricted variant; no CYP450/mechanism data); explanations are associations in name text, not pharmacology.",
    "Dominated by the Moderate class (~80% of training labels); Minor/Major estimates are far less reliable.",
    "No clinical validation, no patient-level inputs (renal/hepatic function, dose, route, age) are considered.",
]


class FeatureEvidence(BaseModel):
    ngram: str
    contribution: float
    appears_in: List[str]


class ReliabilityInfo(BaseModel):
    regime: str
    regime_meaning: str
    confidence_band: str
    held_out_precision_for_this_prediction: Optional[float] = Field(
        None, description="Share of held-out pairs with this predicted class and band that truly had that class")
    base_rate_of_predicted_class: Optional[float] = None
    n_validation_pairs: Optional[int] = None
    regime_macro_f1: Optional[float] = None
    chance_macro_f1: float = 0.33


class ModelInfo(BaseModel):
    name: str
    trained_at_utc: str
    n_training_pairs: int
    training_data_sha256: Dict[str, str]
    validation_report: str = "ml/reports/severity_model_validation.md"


class MLSeverityAdvisory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "ml_severity_advisory"
    advisory_only: bool = True
    clinically_validated: bool = False
    answers_whether_interaction_exists: bool = False
    pair_in_training_data: bool = False
    documented_source_label: Optional[str] = Field(
        None, description="DDInter's own severity for this pair (source data, not a prediction), when the pair was in the training data")
    drug_a: str
    drug_b: str
    normalized_a: str
    normalized_b: str
    display_severity: str = Field(
        ..., description="What a UI should show first: DDInter's documented label when the pair was in the training data, else the ML estimate")
    display_severity_source: str = Field(..., description="'DDInter documented label' or 'ML estimate'")
    agrees_with_documented_label: Optional[bool] = Field(
        None, description="False when DDInter documents this pair and the model's most likely class differs; None when not applicable")
    predicted_severity: str
    probabilities: Dict[str, float]
    runner_up: Optional[str] = None
    reliability: ReliabilityInfo
    explanation: List[FeatureEvidence]
    explanation_text: str
    model: ModelInfo
    limitations: List[str] = Field(default_factory=lambda: list(LIMITATIONS))
    disclaimer: str = DISCLAIMER


_REGIME_MEANING = {
    "both_seen": "both drugs occur in the training data (new pair between known drugs)",
    "one_unseen": "one drug is not in the training data",
    "both_unseen": "neither drug is in the training data (cold start)",
}


def _band(conf: float) -> str:
    return "low" if conf < 0.5 else ("medium" if conf < 0.7 else "high")


class SeverityAdvisor:
    def __init__(self, model: SeverityModel):
        self.model = model

    # --- construction ---------------------------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path] = None) -> Optional["SeverityAdvisor"]:
        if os.getenv("AEGIS_ML_ADVISORY", "true").lower() not in ("true", "1", "yes"):
            return None
        # explicit path / AEGIS_ML_ARTIFACT wins; otherwise the optional restricted (WHO ATC) artifact if it exists locally,
        # else the public name-only artifact. Deleting the restricted file never breaks the application.
        path = Path(path or os.getenv("AEGIS_ML_ARTIFACT") or (RESTRICTED_ARTIFACT if RESTRICTED_ARTIFACT.exists() else DEFAULT_ARTIFACT))
        try:
            return cls(SeverityModel.load(path))
        except (OSError, ValueError, KeyError):
            return None

    # --- helpers --------------------------------------------------------------------------------
    @staticmethod
    def name_text(raw: str) -> str:
        """Same derivation as training (`ingredient_normalized`, falling back to `name_normalized`)."""
        from data_pipeline.names import normalize_name, split_qualifier  # lazy: keeps import light
        base, _ = split_qualifier(raw)
        return normalize_name(base) or normalize_name(raw)

    # --- API ------------------------------------------------------------------------------------
    def recognises(self, name_text: str) -> bool:
        """A drug is recognised if it is in the training data or named in the WHO ATC index shipped in the artifact.
        Anything else (typos, random text, brand-new drugs) must not get a confident estimate."""
        return self.model.knows(name_text) or name_text in self.model.atc_by_name

    def unrecognised(self, drug_a: str, drug_b: str) -> List[str]:
        return [raw for raw in (drug_a, drug_b) if not self.recognises(self.name_text(raw))]

    def advise_pair(self, drug_a: str, drug_b: str) -> Optional[MLSeverityAdvisory]:
        na, nb = self.name_text(drug_a), self.name_text(drug_b)
        if not na or not nb or na == nb:
            return None
        if not (self.recognises(na) and self.recognises(nb)):
            return None  # abstain: "I don't know this drug" (the API explains which one)
        pred = self.model.predict(na, nb)
        seen = (self.model.knows(na), self.model.knows(nb))
        regime = "both_seen" if all(seen) else ("both_unseen" if not any(seen) else "one_unseen")
        band = _band(pred.confidence)
        val = self.model.validation
        row = next((r for r in val.get("reliability", {}).get(regime, [])
                    if r["band"] == band and r["predicted"] == pred.label), None)
        macro = val.get("summary", {}).get(regime, {}).get("model", {}).get("macro_f1", {}).get("mean")
        meta = self.model.meta
        feats = [FeatureEvidence(ngram=f.ngram.replace(" ", "_"), contribution=round(f.contribution, 4),
                                 appears_in=f.drugs) for f in pred.top_features]
        trained = self.model.training_label(na, nb)
        source_label = None if trained is None or trained < 0 else LABELS[trained]
        text = (f"Estimated {pred.label} (confidence {pred.confidence:.2f}, {band}); runner-up {pred.runner_up}. "
                + (f"Name patterns most associated with {pred.label}: " + ", ".join(f"'{f.ngram}'" for f in feats) + ". "
                   if feats else "No name pattern stood out; the estimate mostly reflects class frequencies. ")
                + f"Regime: {_REGIME_MEANING[regime]}.")
        if trained is not None:
            text += (" This pair was part of the training data, so the estimate is NOT an independent prediction"
                     + (f"; DDInter itself lists it as {source_label}." if source_label else "; DDInter lists conflicting levels for these names."))
        elif row and row.get("precision") is not None:
            text += (f" In held-out validation, {row['precision']:.0%} of such predictions were correct "
                     f"(this class makes up {row['base_rate']:.0%} of pairs).")
        agrees = None if source_label is None else (source_label == pred.label)
        if agrees is False:
            text = (f"DDInter documents this pair as {source_label}. The model's most likely class ({pred.label}) DISAGREES; "
                    f"use the documented label and treat the model as unreliable for this pair. ") + text
        return MLSeverityAdvisory(
            display_severity=source_label or pred.label,
            display_severity_source=("DDInter documented label" if source_label else
                                     ("ML estimate (DDInter lists conflicting levels for these names, e.g. different forms of one drug)"
                                      if trained is not None else "ML estimate")),
            agrees_with_documented_label=agrees,
            drug_a=drug_a, drug_b=drug_b, normalized_a=na, normalized_b=nb,
            pair_in_training_data=trained is not None, documented_source_label=source_label,
            predicted_severity=pred.label,
            probabilities={k: round(v, 4) for k, v in pred.probabilities.items()},
            runner_up=pred.runner_up,
            reliability=ReliabilityInfo(
                regime=regime, regime_meaning=_REGIME_MEANING[regime], confidence_band=band,
                held_out_precision_for_this_prediction=None if trained is not None or not row or row["precision"] is None else round(row["precision"], 3),
                base_rate_of_predicted_class=None if not row else round(row["base_rate"], 3),
                n_validation_pairs=None if trained is not None or not row else row["n"],
                regime_macro_f1=None if macro is None else round(macro, 3)),
            explanation=feats, explanation_text=text,
            model=ModelInfo(name=meta["model_name"], trained_at_utc=meta["trained_at_utc"],
                            n_training_pairs=meta["n_training_pairs"],
                            training_data_sha256={k: v for k, v in meta["training_data"].items() if k.endswith("sha256")}),
        )

    def advise_findings(self, findings: Iterable[Any]) -> List[Dict[str, Any]]:
        """One advisory per stored rule finding that documents a pairwise interaction. The findings are only
        read; their severity, text and status are never changed."""
        out: List[Dict[str, Any]] = []
        for f in findings:
            trace = getattr(f, "trace", None) or {}
            pair = trace.get("matched_pair") if isinstance(trace, dict) else None
            title = str(getattr(f, "title", "") or "")
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2 and title.startswith("Documented Interaction")):
                continue
            inputs = getattr(f, "inputs", None) or {}
            a = (inputs.get("drug_a") if isinstance(inputs, dict) else None) or pair[0]
            b = (inputs.get("drug_b") if isinstance(inputs, dict) else None) or pair[1]
            adv = self.advise_pair(str(a), str(b))
            if adv is not None:
                out.append({"finding_id": getattr(f, "id", None), "rule_id": getattr(f, "rule_id", None),
                            "rule_severity": getattr(f, "severity", None), "advisory": adv})
        return out


_lock = threading.Lock()
_cached: Dict[str, Optional[SeverityAdvisor]] = {}


def get_advisor() -> Optional[SeverityAdvisor]:
    """Process-wide advisor (None when disabled/unavailable). Cached per (flag, artifact path)."""
    key = f"{os.getenv('AEGIS_ML_ADVISORY', 'true')}|{os.getenv('AEGIS_ML_ARTIFACT', '')}"
    with _lock:
        if key not in _cached:
            _cached[key] = SeverityAdvisor.load()
        return _cached[key]
