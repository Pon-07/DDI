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

The checked-in v2 report gives five-seed held-out macro-F1 results:

| Evaluation regime | Macro-F1 (mean ± std) |
| --- | ---: |
| Both drugs seen in training | 0.684 ± 0.009 |
| One drug unseen | 0.589 ± 0.011 |
| Both drugs unseen | 0.485 ± 0.024 |

These are model-report metrics, not a clinical performance claim. The evaluation splits by drug identity; the both-seen score may reflect drug-identity signal. The shipped final weights are trained on all labeled pairs and do not have an independent holdout set of their own.

## Limitations and licensing

The model estimates a severity pattern only for a pair whose interaction is already documented elsewhere. It cannot determine whether two drugs interact: the training data have no negative pairs. It is not clinically validated and must not replace rules, source review, professional judgment, dosing guidance, or treatment decisions. The advisory response includes these limits and flags pairs seen in training.

WHO ATC/DDD source data are not included. The repository ignores the raw WHO index; WHO-derived mappings embedded in the artifact may still require licence review before public redistribution. Review the applicable WHO terms before redistributing the artifact or derived mappings.

## Validation

- Focused ML, ATC, data-quality, and drug-validation suite: **85 passed, 1 skipped**.
- Full test suite: **439 passed, 1 skipped** (1,643 warnings, chiefly existing deprecation warnings).
- The test run used the repository's disposable-database isolation fixture; the protected real database files were not modified.
- No model training was run. Metrics above come from the checked-in validation report.

Tests use synthetic fixtures and disposable databases; they must not use `aegis_rx.db*`.
