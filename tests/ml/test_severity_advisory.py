"""Severity advisory: inference correctness, honesty of the output, separation from rule findings, API behaviour."""
import copy
import json
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO = Path(__file__).resolve().parents[2]
ARTIFACT = REPO / "ml" / "artifacts" / "severity_baseline_v2.json"
CLEAN = REPO / "datasets" / "clean"

from ml.inference import LABELS, SeverityModel, char_wb_ngrams
from ml.advisory import SeverityAdvisor, get_advisor
from tests.ml.test_ml_baseline import clean_dir  # noqa: F401  (synthetic fixture, invented drug names)


# ---- inference correctness ------------------------------------------------------------------------
def test_analyzer_matches_scikit_learn():
    from sklearn.feature_extraction.text import TfidfVectorizer
    analyzer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4), lowercase=True).build_analyzer()
    for text in ["warfarin", "a", "ab", "abc", "St  John's wort", "vitamin b-12", "  leading", "tab\tsep", "ÅNGSTROM drug", "x y z"]:
        assert char_wb_ngrams(text) == analyzer(text), text


def test_numpy_inference_reproduces_sklearn_on_a_synthetic_model(clean_dir):
    from ml.data import build_drug_table, load_pairs
    from ml import train_final as tf
    pairs, drugs = load_pairs(clean_dir), build_drug_table(clean_dir)
    cv_stub = {"summary": {}, "reliability": {}}
    artifact, vec, clf = tf.build_artifact(pairs, drugs, clean_dir, 1, cv_stub)
    gap = tf.verify_equivalence(artifact, pairs, drugs, vec, clf, n=40)  # also asserts symmetry
    assert gap < 1e-9


@pytest.mark.skipif(not ARTIFACT.exists(), reason="artifact not built")
class TestShippedArtifact:
    @pytest.fixture(scope="class")
    def model(self):
        return SeverityModel.load(ARTIFACT)

    def test_probabilities_valid_and_symmetric(self, model):
        for a, b in [("warfarin", "fluconazole"), ("lisinopril", "spironolactone"), ("zzzz", "qqqq")]:
            p, q = model.predict_proba(a, b), model.predict_proba(b, a)
            assert p.shape == (3,) and abs(p.sum() - 1) < 1e-9 and (p >= 0).all()
            assert np.allclose(p, q, atol=1e-12)

    def test_artifact_is_plain_json_with_provenance(self):
        art = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        assert art["format"] == "aegis-severity-linear-v2" and art["classifier"]["classes"] == list(LABELS)
        meta = art["metadata"]
        assert meta["n_training_pairs"] == 130422 and "Unknown" in meta["training_data"]["source"]
        assert len(meta["training_data"]["ddinter_pairs_clean.csv_sha256"]) == 64
        assert "deciding whether two drugs interact (no negative examples)" in meta["not_for"]

    @pytest.mark.skipif(not (CLEAN / "ddinter_pairs_clean.csv").exists(), reason="clean data missing")
    def test_artifact_matches_the_clean_dataset_it_was_trained_on(self):
        import hashlib
        art = json.loads(ARTIFACT.read_text(encoding="utf-8"))["metadata"]["training_data"]
        sha = hashlib.sha256((CLEAN / "ddinter_pairs_clean.csv").read_bytes()).hexdigest()
        assert sha == art["ddinter_pairs_clean.csv_sha256"], "dataset changed since the model was trained: retrain (python -m ml.train_final)"

    @pytest.mark.skipif(not (CLEAN / "ddinter_pairs_clean.csv").exists(), reason="clean data missing")
    def test_training_pairs_carry_the_ddinter_label(self, model):
        import pandas as pd
        pairs = pd.read_csv(CLEAN / "ddinter_pairs_clean.csv", dtype=str)
        pairs = pairs[pairs["label_status"] == "labeled"].head(300)
        adv = SeverityAdvisor(model)
        checked = 0
        for r in pairs.itertuples():
            a = adv.advise_pair(r.drug_lo, r.drug_hi)
            if a is None or not a.pair_in_training_data or a.documented_source_label is None:
                continue
            assert a.documented_source_label == r.severity_label, (r.drug_lo, r.drug_hi)
            checked += 1
        assert checked > 200


# ---- advisory object ------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def advisor():
    adv = SeverityAdvisor.load(ARTIFACT) if ARTIFACT.exists() else None
    if adv is None:
        pytest.skip("artifact not built")
    return adv


def test_advisory_is_labelled_explained_and_honest(advisor):
    adv = advisor.advise_pair("fakedrugol", "zorbamine")  # invented names: both unseen
    assert adv.advisory_only is True and adv.clinically_validated is False
    assert adv.answers_whether_interaction_exists is False
    assert adv.reliability.regime == "both_unseen" and adv.reliability.confidence_band in {"low", "medium", "high"}
    assert adv.predicted_severity in LABELS and abs(sum(adv.probabilities.values()) - 1) < 1e-3
    assert adv.explanation and adv.explanation_text and "not been clinically validated" in adv.disclaimer
    assert any("cannot tell whether two drugs interact" in l for l in adv.limitations)
    assert adv.model.n_training_pairs == 130422
    text = adv.explanation_text.lower()
    for forbidden in ("accuracy", "guarantee", "safe to"):
        assert forbidden not in text


def test_pair_in_training_data_is_flagged_not_presented_as_a_validated_prediction(advisor):
    import pandas as pd
    if not (CLEAN / "ddinter_pairs_clean.csv").exists():
        pytest.skip("clean data missing")
    pairs = pd.read_csv(CLEAN / "ddinter_pairs_clean.csv", dtype=str)
    r = pairs[pairs["label_status"] == "labeled"].iloc[0]
    adv = advisor.advise_pair(r.drug_lo, r.drug_hi)
    assert adv.pair_in_training_data and adv.documented_source_label == r.severity_label
    assert adv.reliability.held_out_precision_for_this_prediction is None
    assert "NOT an independent prediction" in adv.explanation_text


def test_same_name_and_empty_input_give_no_advisory(advisor):
    assert advisor.advise_pair("Warfarin", "warfarin") is None
    assert advisor.advise_pair("", "aspirin") is None


def test_advise_findings_reads_but_never_changes_rule_findings(advisor):
    f_ddi = SimpleNamespace(id=7, rule_id="DDI_x", severity="undetermined", title="Documented Interaction: Warfarin + Fluconazole",
                            trace={"matched_pair": ["fluconazole", "warfarin"]}, inputs={"drug_a": "Warfarin", "drug_b": "Fluconazole"})
    f_other = SimpleNamespace(id=8, rule_id="RENAL_1", severity="high", title="Renal risk", trace={}, inputs={})
    f_dup = SimpleNamespace(id=9, rule_id="DUP", severity="moderate", title="Duplicate therapy: A + B",
                            trace={"matched_pair": ["a", "b"]}, inputs={})
    before = copy.deepcopy([vars(f) for f in (f_ddi, f_other, f_dup)])
    out = advisor.advise_findings([f_ddi, f_other, f_dup])
    assert [o["finding_id"] for o in out] == [7]
    assert out[0]["rule_severity"] == "undetermined"
    assert [vars(f) for f in (f_ddi, f_other, f_dup)] == before


# ---- switches --------------------------------------------------------------------------------------
def test_can_be_disabled_and_missing_artifact_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_ML_ADVISORY", "false")
    assert SeverityAdvisor.load() is None and get_advisor() is None
    monkeypatch.setenv("AEGIS_ML_ADVISORY", "true")
    monkeypatch.setenv("AEGIS_ML_ARTIFACT", str(tmp_path / "nope.json"))
    assert get_advisor() is None
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": "something-else"}')
    assert SeverityAdvisor.load(bad) is None


# ---- HTTP API --------------------------------------------------------------------------------------
@pytest.mark.skipif(not ARTIFACT.exists(), reason="artifact not built")
class TestApi(unittest.TestCase):
    def setUp(self):
        from app.main import app
        from tests.test_client import LocalTestClient

        self.app = app
        self.client = LocalTestClient(app)

    def tearDown(self):
        self.app.dependency_overrides.clear()

    def test_status_reports_advisory_limits(self):
        data = self.client.get("/ml/status").json()
        assert data["enabled"] and data["clinically_validated"] is False
        assert set(data["held_out_macro_f1"]) == {"both_seen", "one_unseen", "both_unseen"}
        assert data["held_out_macro_f1"]["both_unseen"] < 0.6

    def test_pair_endpoint_runs_without_an_account(self):
        res = self.client.post("/ml/severity-advisory", json={"drug_a": "fakedrugol", "drug_b": "zorbamine"})
        assert res.status_code == 200
        body = res.json()
        assert "cannot tell whether the drugs interact" in body["notice"]
        assert body["advisory"]["advisory_only"] is True
        assert self.client.post("/ml/severity-advisory", json={"drug_a": "x", "drug_b": "x"}).status_code == 422
        assert self.client.post("/ml/severity-advisory", json={"drug_a": "a", "drug_b": "b", "extra": 1}).status_code == 422

    def test_demo_ui_offers_a_standalone_prediction_form(self):
        response = self.client.get("/app")
        assert response.status_code == 200
        assert "Get advisory" in response.text
        assert "login-form" not in response.text.lower()

    def test_disabled_advisory_does_not_break_health(self):
        os.environ["AEGIS_ML_ADVISORY"] = "false"
        try:
            res = self.client.post("/ml/severity-advisory", json={"drug_a": "aspirin", "drug_b": "ibuprofen"})
            assert res.status_code == 503
            assert self.client.get("/ml/status").json()["enabled"] is False
            assert self.client.get("/health").status_code == 200
        finally:
            os.environ.pop("AEGIS_ML_ADVISORY", None)
