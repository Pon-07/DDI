# AEGIS Rx (DDI) - Autonomous Medication Safety Platform

Deterministic medication-safety rules + a drug-knowledge base built from DDInter, openFDA and (planned) DailyMed.
This README documents the **dataset audit and cleaning pass** (2026-10-19): what the data looks like, what was fixed,
and what is still blocked. No ML model has been trained.

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

### DDInter (only category file **A** is in the repo; A,B,D,H,L,P,R,V expected)

| metric | value |
|---|---|
| raw rows (kept verbatim) | 56,367 |
| quarantined rows | 0 |
| unordered pairs | 56,367 (0 reversed / 0 same-direction duplicates, 0 severity conflicts) |
| drugs (DDInter IDs) | 1,757 |
| **labelled** pairs (trainable) | **41,600** = Moderate 33,057 / Major 4,906 / Minor 3,637 |
| `Unknown` pairs (NOT a label) | 14,767 |

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

## ML readiness (no model trained)

**Not ready for a feature-based severity model.** Labels exist (41,600, 79.5% Moderate) but there are no drug-level features:

| need | status |
|---|---|
| RxNorm RxCUI | 0 / 1,757 - no source in repo (left NULL) |
| ATC codes | 0 / 1,757 |
| CYP450 substrate/inhibitor/inducer | 0 / 1,757 |
| DrugBank IDs | 0 / 1,757 |
| Interaction text / mechanism | none (CSV has only a level) |
| Negative (non-interacting) pairs | none - only interaction-vs-severity is possible, not interaction detection |
| DDInter categories B,D,H,L,P,R,V | missing (1 of 8 files) |

Use drug-disjoint (cold-start) splits; random pair splits leak drug identity.

## Remaining issues / blockers

1. Git-LFS raw openFDA file (632 MB) and parts 2-14 are not available; names cannot be recovered for the 15,951 unnamed labels without them.
2. DDInter files B,D,H,L,P,R,V and the DailyMed archive must be downloaded and placed in `data_validation/` (the build environment had no access to those hosts).
3. RxNorm / ATC / CYP450 / DrugBank sources are not in the repo (licences or downloads required).
4. **Secrets in git history:** `aegis_rx.db-wal` was committed and contains TOTP secrets, sessions and OTP records. Run `bash scripts/repo_hygiene.sh`
   (untracks the DB files), then follow its printed steps: rotate secrets, set `AEGIS_DEMO_MODE=false`, change `AEGIS_DEMO_DEFAULT_OTP`,
   purge history with `git filter-repo`, force-push. `scripts/sanitize_local_db.py` produces a share-safe copy of a DB.
5. Known failing test (pre-existing, unrelated): `tests/test_step47_demo_runtime.py::test_demo_html_interface` expects "AEGIS Rx" in the HTML, which still says MICROMEDX.
6. `estrone` vs `estrone sulfate` still normalise to one name (flagged, not merged blindly).

## Tests

`pytest -q` -> 437 passed, 1 failed (the pre-existing branding test above). New tests: `tests/data_quality/`, `tests/test_normalizer_collisions.py`,
`tests/test_ddinter_adapter_reversed_pairs.py`, `tests/test_sanitize_local_db.py` (94 tests). They check DDInter counts against the CSV independently,
reproducibility (two builds identical), production DB untouched, no fabricated features, requirements encoding and no tracked DB files / hardcoded secrets.
The hygiene tests fail on the repo as shipped and pass after `scripts/repo_hygiene.sh`.
