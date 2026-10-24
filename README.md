# DDI severity prediction and advisory

This repository centers on a reproducible drug–drug interaction severity model. The eight DDInter categories (`A`, `B`, `D`, `H`, `L`, `P`, `R`, `V`) are processed by the data pipeline. The shipped (public) v2.1 artifact uses normalized drug-name character n-grams as symmetric pair features. A name + WHO ATC variant exists but is kept local and git-ignored while the WHO licence question is open (see `docs/WHO_ATC_DEPENDENCIES.md`).

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

The checked-in v2.1 report (public, name-only) gives five-seed held-out macro-F1 results (ingredient-grouped splits, see below):

| Evaluation regime | Macro-F1 (mean ± std) |
| --- | ---: |
| Both drugs seen in training | 0.673 ± 0.009 |
| One drug unseen | 0.554 ± 0.011 |
| Both drugs unseen (cold start) | 0.429 ± 0.016 |

A restricted name + WHO ATC variant is not shipped; its comparative results are withheld pending licensing review. Cold-start recall of `Major` is only 0.41 for the public model.

Splits hold out whole **ingredients** (same RxCUI, else same normalised name) and whole ingredient pairs, because DDInter lists 107 ingredient names under 243 ids (route/form variants) that the model sees as identical. Two ids are one drug when they share the ingredient name OR the RxCUI (connected components; e.g. iobenguane I-123 and I-131 share a name but not an RxCUI). An earlier version grouped by RxCUI first and let those two straddle train and test; that is fixed and covered by `tests/ml/test_grouped_split.py`.

These are model-report metrics, not a clinical performance claim. The both-seen score may reflect drug-identity signal. The shipped final weights are trained on all labeled pairs and do not have an independent holdout set of their own.

## How the advisory behaves (read before using the output)

- If DDInter documents the pair, the response shows **DDInter's label first** (`display_severity`, `display_severity_source`). If the model's most likely class differs, `agrees_with_documented_label` is `false` and the text says the model DISAGREES. Example: warfarin + fluconazole is documented Major; the baseline's top class is Minor (0.47 vs Major 0.40, low confidence), so the documented Major is what must be shown.
- A drug that is not recognized by the active artifact gets **no estimate**. The API returns HTTP 422 (`Drug not recognised`) for unknown drugs; the name-only public artifact recognizes DDInter training names, while a restricted artifact can also recognize names in its WHO ATC lookup.
- `SeverityAdvisor.load()` selects the local `ml/artifacts/restricted/severity_baseline_v2_atc.json` automatically when that ignored file exists; otherwise it loads the committed name-only artifact. Consequently, local predictions and recognized names can differ depending on whether the restricted artifact is present. Check `/ml/status` (`uses_who_atc_data`) to see which kind is active.
- Pairs whose names collapse several forms of one drug with conflicting DDInter levels (2,058 of 130,422 labelled id-pairs, e.g. live vs inactivated typhoid vaccine) are marked as such and show the ML estimate, not a single documented label.
- The estimate cannot tell whether two drugs interact and does not use the patient.

## Identifier cross-references (RxNorm, ClinPGx)

`data_pipeline/crosswalk.py` links DDInter drugs to identifiers by **exact normalised name only** (no fuzzy matching). Place the source files in `data_validation/external/` (git-ignored, not committed): the RxNorm Current Prescribable Content zip and ClinPGx `drugs.zip` (both available in the Kawinkrish/data repository). Then run `python -m data_pipeline.build --no-legacy`.

| field | coverage | rule |
|---|---|---|
| RxCUI | 1,744 / 1,939 (RxNorm 1,516 + ClinPGx 228) | RxNorm ingredient concepts (IN, PIN) only; a name with more than one RxCUI is skipped (16 drugs), never guessed |
| ATC | Not included in this public candidate | WHO-derived material; licensing unresolved; coverage figures withheld pending review |
| CYP450 role | 0 | **not available**: ClinPGx CYP rows are pharmacogenomic gene-chemical associations, not substrate/inhibitor/inducer roles, so they are deliberately not used |

The provenance of every value is in the staging tables `drug_rxcui` and `drug_atc`. RxCUI is an identifier only; the model does not use it as a feature (it is used to group synonyms in the evaluation splits). Without the external files the build still runs and those columns stay empty. Licences: DDInter, ClinPGx and the community ATC snapshot are CC BY-NC-SA 4.0 (non-commercial, share-alike); RxNorm and WHO ATC carry their own terms.

## Limitations and licensing

The model estimates a severity pattern only for a pair whose interaction is already documented elsewhere. It cannot determine whether two drugs interact: the training data have no negative pairs. It is not clinically validated and must not replace rules, source review, professional judgment, dosing guidance, or treatment decisions. The advisory response includes these limits and flags pairs seen in training.

WHO ATC/DDD data are **not** included: not the raw index, not the ATC column of the clean CSV, and not the artifact (the committed artifact is name-only). `docs/WHO_ATC_DEPENDENCIES.md` lists every file that contains or depends on WHO-derived data, how it is isolated, what is lost without it, and replacement options that are not yet adopted. Earlier commits on GitHub already contain WHO-derived ATC values (see that document). This is not a licensing determination.

## Validation

- Focused data-quality/ML tests: **30 passed, 1 skipped, 4 API methods deselected**.
- Broad non-API run: **428 passed, 5 skipped, 23 API-client tests deselected** (1,487 warnings). The full suite and API-client tests remain unverified.
- On this Windows environment, a diagnostic stack dump showed the API test client blocks in asyncio's socket-pair initialization before the application handles the request. Run the unchanged API tests on a normal Windows host with `python -m pytest -q tests/ml/test_severity_advisory.py::TestApi`.
- Test database URLs are redirected to disposable storage by `tests/conftest.py`; the protected real database files were not modified.
- The shipped v2.1 artifact is the name-only public model. WHO ATC licensing remains unresolved; restricted data, lookup files, and restricted-model metrics are not included in this candidate.

Tests use synthetic fixtures and disposable databases; they must not use `aegis_rx.db*`.
