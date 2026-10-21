# AEGIS Rx (DDI) - Autonomous Medication Safety Platform

Deterministic medication-safety rules + a drug-knowledge base built from DDInter, openFDA and (planned) DailyMed.
This README documents the **dataset audit and cleaning pass** (2026-10-19): what the data looks like, what was fixed,
and what is still blocked. It also documents the **severity advisory** (an explainable, advisory-only ML estimate kept apart from the rule engine) and the **authentication hardening**.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/init_db.py                 # creates a fresh local DB (aegis_rx.db is NOT tracked in git)
python -m data_pipeline.build             # rebuilds staging DB, datasets/clean/*.csv, reports/data_quality/*
pytest -q                                 # full suite
```

The pipeline never writes to `aegis_rx.db`. It records SHA-256 fingerprints of the production DB files before and
after and fails the build if they differ. The staging DB (`staging/aegis_staging.db`, ~158 MB) is git-ignored.
Use `AEGIS_DATABASE_URL=sqlite:////path/to/other.db` to point the app/ingest scripts at a disposable database.

## Dataset statistics (from `reports/data_quality/data_quality_report.md`)

### Databases as shipped (before)

| database | drugs | interaction_evidence | label_evidence | notes |
|---|---|---|---|---|
| `aegis_rx.db` (live) | 0 | 0 | 0 | knowledge was never ingested into it; it only holds users/sessions/OTPs |
| `aegis_rx.db.contaminated-backup` | 3,126 | 56,367 | 8,997 | test-fixture clinical rows, 768 duplicate label-text groups, `description == severity` on all 56,367 interactions, 0 RxNorm IDs |

### DDInter (all 8 category files A,B,D,H,L,P,R,V)

| metric | value |
|---|---|
| raw rows (kept verbatim) | 8 files, 0 quarantined |
| unordered pairs | 160,235 (43,826 same-direction duplicates merged, 0 severity conflicts) |
| drugs (DDInter IDs) | 1,939 |
| **labelled** pairs (trainable) | **130,422** = Moderate 96,675 / Major 26,914 / Minor 6,833 |
| `Unknown` pairs (NOT a label) | 29,813 |
| drugs with a WHO ATC code | 1,619 of 1,939 (83.5%; exact normalised-name match only, no fuzzy matching) |

### openFDA (only part 1 of 14 is in the repo; the raw JSON is a Git-LFS pointer)

| metric | value |
|---|---|
| source rows | 20,000 |
| labels with a drug name | 4,049 (3,910 ingestible = named + at least one section) |
| labels **without** any drug name | 15,951 (79.8%) - missing in the source extract; names are never guessed from prose |
| section rows on ingestible labels | 8,997 (identical to the legacy backup) |
| section rows incl. unnamed labels (kept in staging only) | 37,197 |
| renal / hepatic keyword flags | 3,279 / 2,668 (keyword flags, not dose rules) |

### DailyMed

0 labels ingested - the monthly SPL archive is not in the repo. The parser (`data_pipeline/dailymed.py`, LOINC-based,
rejects DOCTYPE/ENTITY, size-capped) is tested on synthetic SPL and ready.

## Cleaning steps

1. **Raw is preserved.** Every source row lands untouched in `ddinter_raw` / `openfda_labels` (+ `source_files` with SHA-256).
   Clean tables are *derived* and point back to raw rows; nothing is deleted - invalid rows go to quarantine with a reason.
2. **DDInter pairs are unordered**, keyed by numeric DDInter ID, so A-B and B-A collapse to one pair without inventing reverse rows.
   `ddinter_adapter.py` had the same bug (case/order-sensitive key); fixed with an order-insensitive key.
3. **Labels:** `Unknown` is kept as status `unknown`, never as a training label. Conflicting levels would get no label (never "max").
   The legacy `description == severity` echo is not carried forward.
4. **Normaliser fixes** (`agents/knowledge/normalizer.py`): leading cations (calcium, sodium, potassium, ...) are no longer stripped as
   salts, and numbers inside tokens (`i-123`, `b-12`) are kept. Before the fix different drugs collapsed to one name; now the only
   remaining collision is `estrone` / `estrone sulfate`, which is logged as an error and not auto-merged.
5. **openFDA:** unnamed labels are flagged, not named; multi-product names (7) are kept verbatim; sections keep a text SHA-256.
6. **Cross-reference:** DDInter drugs link to openFDA labels by exact normalised name only (523 of 1,757 drugs); no fuzzy matching.

## Clean outputs

`datasets/clean/`: `ddinter_pairs_clean.csv`, `ddinter_drugs_clean.csv`, `openfda_labels_clean.csv`, `quality_issues.csv`, `feature_coverage.csv`.
Full machine-readable report: `reports/data_quality/data_quality_report.json`.

## ML readiness

**Limited readiness.** Labels exist (130,422, 74% Moderate) and drug features are only name text plus WHO ATC (used when the licensed WHO file is placed in `data_validation/`; it is git-ignored and never committed):

| need | status |
|---|---|
| RxNorm RxCUI | 0 / 1,939 - no source in repo (left NULL) |
| ATC codes | 1,619 / 1,939 (WHO ATC/DDD 2026-04-25, exact name, 7-character substance codes only) |
| CYP450 substrate/inhibitor/inducer | 0 / 1,939 |
| DrugBank IDs | 0 / 1,939 |
| Interaction text / mechanism | none (CSV has only a level) |
| Negative (non-interacting) pairs | none - only interaction-vs-severity is possible, not interaction detection |
| DDInter categories | all 8 present |

Use drug-disjoint (cold-start) splits; random pair splits leak drug identity.

## Severity advisory (ML, advisory only)

A baseline model estimates the severity (Minor / Moderate / Major) of an interaction **that is already documented**.
It never replaces, re-grades or suppresses a rule-based finding: the rule engine (`agents/risk`) is unchanged and the
advisory is a separate object returned by separate endpoints. It is **not clinically validated**.

| | |
|---|---|
| Model | balanced logistic regression on symmetric pair features (`[a+b, a*b]`) built per drug from TF-IDF of character 3-4-grams of the normalised ingredient name plus binary WHO ATC level 1-4 indicators (zeros when a drug has no ATC match) |
| Training data | 130,422 labelled DDInter pairs from all 8 categories (`Unknown` excluded); artifact `ml/artifacts/severity_baseline_v2.json` (plain JSON, no pickle), numpy-only inference in `ml/inference.py` |
| Explanation | per-prediction n-gram contributions (exact additive decomposition of the winning logit), probabilities, runner-up, regime, held-out precision of that kind of prediction, limitations |
| Not for | deciding whether two drugs interact (no negative examples), dosing, overriding rule findings |

Held-out results (5 seeds; mean ± std; chance macro-F1 is about 0.33; full tables in `ml/reports/severity_model_validation.md`):

| regime | meaning | macro-F1 | recall Minor / Moderate / Major |
|---|---|---|---|
| `both_seen` | new pair, both drugs seen in training | 0.684 ± 0.009 | 0.89 / 0.76 / 0.84 |
| `one_unseen` | one drug never seen | 0.589 ± 0.011 | 0.60 / 0.74 / 0.67 |
| `both_unseen` | cold start | 0.485 ± 0.024 | 0.35 / 0.73 / 0.49 |

Name-only features on the same splits scored 0.674 / 0.565 / 0.447, so ATC helps mainly for unseen drugs
(+0.04 cold start, +0.02 one-unseen). The previous v1 model (category A only) scored 0.685 / 0.581 / 0.445, but v1 and v2
are **not like-for-like**: the class mix changed (Major about 21% of labels versus 12%) and the split populations differ.
Cold-start performance is still weak (macro-F1 0.485, Minor recall 0.35), so treat the advisory for new drugs with caution. The `both_seen` figure mostly reflects drug-identity
signal, not pharmacology. The shipped weights are fit on all labelled pairs and have no held-out set of their own.
If a pair was part of the training data the advisory says so and shows DDInter's own label instead of presenting the
estimate as a prediction.

Endpoints (all under `/ml`, advisory only): `GET /ml/status` (public, no patient data), `POST /ml/severity-advisory`
and `GET /ml/patients/{id}/severity-advisories` (login required; the latter reads stored rule findings, changes
nothing and writes an `ML_SEVERITY_ADVISORY_VIEWED` audit event). `AEGIS_ML_ADVISORY=false` switches it off; a missing
artifact disables it silently and the rule engine is unaffected. `AEGIS_ML_ARTIFACT` points at another artifact.

Retrain (needs `requirements-ml.txt`; reproduces the report and checks numpy inference against scikit-learn to 1e-9):

```bash
python -m ml.train_final --clean-dir datasets/clean --seeds 5     # --features auto|name|name_atc
```

A test fails if `datasets/clean/ddinter_pairs_clean.csv` changes without retraining. Features that would genuinely
improve it further (RxCUI, CYP450, interaction text, negative pairs) are still missing; see below.

**WHO licence:** the artifact embeds a small name-to-ATC lookup derived from the WHO ATC/DDD index. Check WHO's terms before redistributing the artifact or the clean drug CSV (`atc_codes` column) publicly.

### Adding more DDInter files (all 8 are already integrated)

To add or refresh category files, download `ddinter_downloads_code_B/D/H/L/P/R/V.csv` from DDInter, then (nothing is overwritten):

```bash
python scripts/add_ddinter_files.py PATH/TO/ddinter_downloads_code_B.csv ... --dry-run   # validate + preview pair/conflict counts
python scripts/add_ddinter_files.py PATH/TO/ddinter_downloads_code_B.csv ...             # copy into data_validation/
python -m data_pipeline.build        # rebuild clean CSVs/report (pairs repeated across files merge; disagreements stay unlabelled)
python -m ml.train_final             # retrain + re-validate; compare ml/reports with the previous version before trusting it
python -m pytest -q
```

Check the DDInter licence before publishing the redistributed clean CSVs. Any clinical claim needs pharmacist review.

## Authentication and demo mode

Secure by default: `AEGIS_DEMO_MODE` defaults to `false` and there is no default OTP or TOTP secret in the code.

| variable | effect |
|---|---|
| `AEGIS_DEMO_MODE=true` | enables demo behaviour: preview OTP, demo TOTP preview endpoint, free role selection for demo accounts |
| `AEGIS_DEMO_DEFAULT_OTP` | optional fixed demo OTP; used only in demo mode, only if it is a non-trivial 6-digit code, only for the four seeded demo accounts. Never commit it |
| `AEGIS_COOKIE_SECURE=true` | mark session cookies `Secure` (set when served over HTTPS) |

Fixed in this pass: a request body could switch demo mode on and receive the OTP; `/auth/totp/demo-code/<phone>` returned
the live TOTP code of any account without authentication; the fixed OTP opened any phone number; unauthenticated TOTP
enrolment returned the secret of an enrolled account and rewrote its role; login and session switching could escalate
to Administrator. Each has a regression test in `tests/test_auth_hardening.py`.

Still open (not changed): TOTP secrets are stored unencrypted in the DB; OTP hashing uses a fixed salt; there is no
rate limiting on login endpoints; `GET /auth/totp/status/<phone>` reveals whether an account exists; unauthenticated
self-enrolment still creates non-administrator accounts; most non-auth routers (`/findings`, `/patients`, ...) do not
require a session; the secrets already published in git history stay valid in any copy until rotated.

## Remaining issues / blockers

1. Git-LFS raw openFDA file (632 MB) and parts 2-14 are not available; names cannot be recovered for the 15,951 unnamed labels without them.
2. The DailyMed archive must be downloaded and placed in `data_validation/` (the build environment had no access to that host).
3. RxNorm / CYP450 / DrugBank sources are not in the repo (licences or downloads required). ATC needs the WHO file in `data_validation/` (git-ignored; 16.5% of drugs have no exact-name match).
4. **Secrets in git history:** `aegis_rx.db-wal` was committed and contains TOTP secrets, sessions and OTP records. Run `bash scripts/repo_hygiene.sh`
   (untracks the DB files), then follow its printed steps: rotate secrets, set `AEGIS_DEMO_MODE=false`, change `AEGIS_DEMO_DEFAULT_OTP`,
   purge history with `git filter-repo`, force-push. `scripts/sanitize_local_db.py` produces a share-safe copy of a DB.
5. Branding is still mixed: the `/demo` page now says AEGIS Rx, but MICROMEDX remains in the main workspace (`web_app.py`), the TOTP issuer (kept so existing authenticator entries keep working) and e-mail domains. Pick one name and change them together.
6. Three name collisions are flagged, not merged blindly: `estrone`/`estrone sulfate`, `silver`/`silver nitrate`, `edetate disodium anhydrous`/`edetate calcium disodium anhydrous`.
7. Every severity label is DDInter's own; no pharmacist has reviewed the model. Pharmacist review is required before any clinical claim.

## Tests

`pytest -q` -> 533 passed, 0 failed (full 8-file DDInter data, WHO ATC file present). New tests: `tests/data_quality/`, `tests/test_normalizer_collisions.py`,
`tests/test_ddinter_adapter_reversed_pairs.py`, `tests/test_sanitize_local_db.py` (94 tests). They check DDInter counts against the CSV independently,
reproducibility (two builds identical), production DB untouched, no fabricated features, requirements encoding and no tracked DB files / hardcoded secrets.
The hygiene tests fail on the repo as shipped and pass after `scripts/repo_hygiene.sh`.
