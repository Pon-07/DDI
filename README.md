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
