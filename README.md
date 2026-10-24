# DDI severity prediction and advisory

This repository centers on a reproducible drug–drug interaction severity model. The eight DDInter categories (`A`, `B`, `D`, `H`, `L`, `P`, `R`, `V`) are processed by the data pipeline. The shipped v2 artifact uses normalized drug-name character n-grams and WHO ATC levels 1–4 as symmetric pair features.

## Run locally

Install the model and API dependencies:

```powershell
python -m pip install -r requirements.txt -r requirements-ml.txt
```

Start the local API and demo page:

```powershell
$env:AEGIS_DATABASE_URL = "sqlite:///./staging/ddi-local.db"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/app`. The demo calls `GET /ml/status` and `POST /ml/severity-advisory`; neither endpoint requires an account. Keep the service bound to localhost. This project has no authentication, so do not expose it to a network or enter patient identifiers or protected health information.

Example request:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/ml/severity-advisory `
  -ContentType 'application/json' `
  -Body '{"drug_a":"warfarin","drug_b":"fluconazole","interaction_documented":true}'
```

## Data and reproducibility

The cleaned training inputs are `datasets/clean/ddinter_drugs_clean.csv` and `datasets/clean/ddinter_pairs_clean.csv`. The report records 130,422 labeled pairs over 1,782 drugs; `Unknown` pairs are excluded as labels. Rebuild the data-quality outputs without inspecting any legacy database:

```powershell
python -m data_pipeline.build --no-legacy
```

Retraining is optional; the v2 artifact and reports are already included. To reproduce training and evaluation when the required cleaned data are present:

```powershell
python -m ml.train_final --features auto
```

The training script writes the artifact and validation reports. Do not retrain merely to run inference or the API.

## Alternative ranker

`agents/resolution/alternatives.py` proposes replacement drugs for a flagged drug, ranked by a transparent weighted score (interaction risk 0.60, ATC-class closeness 0.25, dosing-evidence availability 0.15; weights and penalties live in `RANKER_CONFIG`). There is no LLM and no trained model in the decision. It is optional: `SafetyResolutionEngine(alternative_ranker=AlternativeRanker())`; by default nothing changes. Results appear as `result.alternatives` and every item carries `requires_cosign=True`.

- **Risk gate:** full ranked alternatives only for HIGH risk (finding severity critical/contraindicated/major/review_required/high, or a DDInter known Major for the flagged pair). MODERATE gives a monitoring action plus an optional secondary list of at most 3. LOW or undetermined gives no alternatives and says why. Duplicate therapy returns a "consolidate duplicate therapy" action instead of substitutes.
- **Candidates:** same ATC level-4 class (level 3 is added only if fewer than 3 level-4 candidates are free of a known Major; level 4 always ranks first). Active drugs, the flagged drug, identical RxCUI/ingredient and combination products are excluded. Each candidate is checked against every other active drug.
- **Order:** known Major last (and flagged); then level 4 before 3; then known Minor, known Moderate, unknown, ML-raised caution; then score; then name.
- **ML advisory:** for pairs DDInter does not label, the existing severity advisor's estimate is displayed with probabilities, confidence band and regime. It can only raise caution (a non-low-confidence Major lowers rank and flags pharmacist review). Minor/Moderate estimates never improve rank or make a pair "safe"; low-confidence estimates are shown as unreliable and change nothing. If the artifact is missing the ranker works without it.
- **Coverage:** `python scripts/alternative_ranker_coverage.py` regenerates `reports/alternative_ranker/coverage.md` (aggregate counts over the DDInter Major pairs; no drug names or ATC codes).

Limits: ATC class is not clinical equivalence (route, form, indication, potency and patient suitability are not checked). A pair absent from DDInter is unknown, not safe. The dosing component only says whether label evidence exists (keyword flags from the openFDA clean data); it never proposes or derives a dose. The ranker has not been clinically validated, and a pharmacist must review every suggestion. It reads the clean CSVs at runtime and does not copy ATC data anywhere else.

## Audited Step 5 delivery scope

The reviewed bundle target is `354ccec49507f8dbbce3624483eb18f57becd842`, descended from prerequisite `23d384216ac2c06d8f2fed5867fd4987487172b3`. Its complete base-relative change list has 40 paths (the target commit itself changes 33). The original `changed.txt` / `files/` overlay covers 22 paths and omits the following 11 data files from its list; they are present in the target and included here:

- `data_validation/ddinter_downloads_code_B.csv`
- `data_validation/ddinter_downloads_code_D.csv`
- `data_validation/ddinter_downloads_code_H.csv`
- `data_validation/ddinter_downloads_code_L.csv`
- `data_validation/ddinter_downloads_code_P.csv`
- `data_validation/ddinter_downloads_code_R.csv`
- `data_validation/ddinter_downloads_code_V.csv`
- `datasets/clean/ddinter_drugs_clean.csv`
- `datasets/clean/ddinter_pairs_clean.csv`
- `datasets/clean/feature_coverage.csv`
- `datasets/clean/quality_issues.csv`

The seven category files complete DDInter categories B, D, H, L, P, R, V; category A was already present at the prerequisite. The four clean CSVs are updated outputs. The complete 40-path list follows:

- `.gitignore`
- `README.md`
- `ROTATE.md`
- `app/routers/demo.py`
- `data_pipeline/atc.py`
- `data_pipeline/build.py`
- `data_pipeline/features.py`
- `data_pipeline/staging.py`
- `data_validation/ddinter_downloads_code_B.csv`
- `data_validation/ddinter_downloads_code_D.csv`
- `data_validation/ddinter_downloads_code_H.csv`
- `data_validation/ddinter_downloads_code_L.csv`
- `data_validation/ddinter_downloads_code_P.csv`
- `data_validation/ddinter_downloads_code_R.csv`
- `data_validation/ddinter_downloads_code_V.csv`
- `datasets/clean/ddinter_drugs_clean.csv`
- `datasets/clean/ddinter_pairs_clean.csv`
- `datasets/clean/feature_coverage.csv`
- `datasets/clean/quality_issues.csv`
- `ml/advisory.py`
- `ml/artifacts/severity_baseline_v2.json`
- `ml/data.py`
- `ml/inference.py`
- `ml/reports/severity_model_validation.json`
- `ml/reports/severity_model_validation.md`
- `ml/train_final.py`
- `reports/data_quality/data_quality_report.json`
- `reports/data_quality/data_quality_report.md`
- `scripts/add_ddinter_files.py`
- `scripts/rotate_auth_credentials.py`
- `tests/data_quality/test_add_ddinter_files.py`
- `tests/data_quality/test_atc.py`
- `tests/data_quality/test_staging_build.py`
- `tests/ml/test_severity_advisory.py`
- `tests/test_auth_flow.py`
- `tests/test_multiuser_auth_isolation.py`
- `tests/test_rotate_auth_credentials.py`
- `tests/test_sms_providers.py`
- `tests/test_step47_demo_runtime.py`
- `tests/test_totp_auth.py`

The delivered ML-only source tree is a follow-up to that audited target. The exact Step 5 and final validation state is summarized in the ZIP delivery manifest.

## Model results

The checked-in v2.1 report gives five-seed held-out macro-F1 results (ingredient-grouped splits, see below):

| Evaluation regime | Macro-F1 (mean ± std) |
| --- | ---: |
| Both drugs seen in training | 0.684 ± 0.009 |
| One drug unseen | 0.585 ± 0.008 |
| Both drugs unseen (cold start) | 0.488 ± 0.019 |

Name-only features score 0.676 / 0.559 / 0.446 on the same splits, so WHO ATC helps mainly for unseen drugs. Cold-start recall of `Major` is still only 0.48.

Splits hold out whole **ingredients** (same RxCUI, else same normalised name) and whole ingredient pairs, because DDInter lists 107 ingredient names under 243 ids (route/form variants) that the model sees as identical. With 5 seeds this changed the scores by less than their standard deviation (per-id splits: 0.687 / 0.590 / 0.487), so the earlier numbers were not materially inflated; the stricter split is kept anyway and is covered by `tests/ml/test_grouped_split.py`.

These are model-report metrics, not a clinical performance claim. The both-seen score may reflect drug-identity signal. The shipped final weights are trained on all labeled pairs and do not have an independent holdout set of their own.

## Identifier cross-references (RxNorm, ClinPGx)

`data_pipeline/crosswalk.py` links DDInter drugs to identifiers by **exact normalised name only** (no fuzzy matching). Place the source files in `data_validation/external/` (git-ignored, not committed): the RxNorm Current Prescribable Content zip and ClinPGx `drugs.zip` (both available in the Kawinkrish/data repository). Then run `python -m data_pipeline.build --no-legacy`.

| field | coverage | rule |
|---|---|---|
| RxCUI | 1,744 / 1,939 (RxNorm 1,516 + ClinPGx 228) | RxNorm ingredient concepts (IN, PIN) only; a name with more than one RxCUI is skipped (16 drugs), never guessed |
| ATC | 1,684 / 1,939 (WHO 1,619 + ClinPGx 65) | ClinPGx fills only drugs the WHO file did not match, 7-character codes, unique drug record |
| CYP450 role | 0 | **not available**: ClinPGx CYP rows are pharmacogenomic gene-chemical associations, not substrate/inhibitor/inducer roles, so they are deliberately not used |

The provenance of every value is in the staging tables `drug_rxcui` and `drug_atc`. RxCUI is an identifier only; the model does not use it as a feature (it is used to group synonyms in the evaluation splits). Without the external files the build still runs and those columns stay empty. Licences: DDInter, ClinPGx and the community ATC snapshot are CC BY-NC-SA 4.0 (non-commercial, share-alike); RxNorm and WHO ATC carry their own terms.

## Limitations and licensing

The model estimates a severity pattern only for a pair whose interaction is already documented elsewhere. It cannot determine whether two drugs interact: the training data have no negative pairs. It is not clinically validated and must not replace rules, source review, professional judgment, dosing guidance, or treatment decisions. The advisory response includes these limits and flags pairs seen in training.

WHO ATC/DDD source data are not included. The repository ignores the raw WHO index; WHO-derived mappings embedded in the artifact may still require licence review before public redistribution. Review the applicable WHO terms before redistributing the artifact or derived mappings.

## Validation

- Full test suite: **445 passed, 0 failed** (with the WHO and external files present; the data-quality tests also pass without them).
- The test run used the repository's disposable-database isolation fixture; the protected real database files were not modified.
- The v2.1 artifact was retrained for this change (5 seeds); metrics above come from `ml/reports/severity_model_validation.md`.

Tests use synthetic fixtures and disposable databases; they must not use `aegis_rx.db*`.
