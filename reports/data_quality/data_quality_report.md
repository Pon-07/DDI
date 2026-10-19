# Data quality report

Pipeline v1.0.0 - generated 2026-10-19T05:09:22Z

## Production safety

Production DB files unchanged by the build: **True** (SHA-256 before/after).

## Before (databases as shipped)

| database | integrity | drugs | interaction_evidence | label_evidence | safety_rules |
|---|---|---|---|---|---|
| production_db | ok | 0 | 0 | 0 | 0 |
| legacy_backup | ok | 3,126 | 56,367 | 8,997 | 0 |

Legacy backup details:

- interactions_matching_source_csv: 56,367
- interactions_not_in_source_csv: 0
- source_csv_pairs_missing_from_db: 0
- interaction_description_equals_severity: 56,367
- label_evidence_duplicate_text_groups: 768
- test_fixture_patients: 4
- drugs_with_rxnorm: 0
- clinical_rows (should be 0 in a knowledge DB): {'patients': 4, 'medications': 1, 'labs': 1, 'orders': 1, 'events': 0, 'findings': 0, 'audit_ledger': 0}

Production DB sensitive-table rows: {'users': 5, 'user_sessions': 12, 'otp_records': 10}; users with a TOTP secret: 4

## After: DDInter

| metric | value |
|---|---|
| raw rows (verbatim, all kept) | 56,367 |
| quarantined rows | 0 |
| unordered pairs | 56,367 |
| drugs (DDInter IDs) | 1,757 |
| pairs by status | {'unknown': 14767, 'labeled': 41600} |
| pairs by label | {'NULL': 14767, 'Moderate': 33057, 'Major': 4906, 'Minor': 3637} |
| reversed duplicate pairs merged | 0 |
| same-direction duplicates merged | 0 |
| severity conflicts (unlabelled) | 0 |
| files present / expected | A / A,B,D,H,L,P,R,V |
| distinct base drugs merged by normaliser | 1 |

Legacy `ddinter_normalized.json` check: {'json_rows': 56367, 'json_rows_matching_csv': 56367, 'json_rows_not_in_csv': 0, 'csv_rows_not_in_json': 0, 'description_equals_severity': 56367}

## After: openFDA

| metric | value |
|---|---|
| source rows (part files present) | 20,000 |
| labels with a drug name | 4,049 |
| labels WITHOUT a drug name | 15951 (79.8%) |
| ingestible labels (named + >=1 section) | 3,910 |
| section rows | 37,197 |
| sections by type | {'dosage_and_administration': 18956, 'contraindications': 6724, 'drug_interactions': 5092, 'warnings_and_cautions': 3380, 'use_in_specific_populations': 3045} |
| renal / hepatic keyword flags true | 3279 / 2668 |
| multi-product names | 7 |
| raw parts present / expected | 1 / 14 |

DailyMed labels ingested: **0** (missing). DDInter-to-openFDA exact-name links: 2337.

## Staging table row counts

| table | rows |
|---|---|
| source_files | 26 |
| ddinter_raw | 56,367 |
| ddinter_drugs | 1,757 |
| ddinter_pairs | 56,367 |
| ddinter_pair_rows | 56,367 |
| openfda_labels | 20,000 |
| openfda_sections | 37,197 |
| dailymed_labels | 0 |
| dailymed_sections | 0 |
| drug_xref | 2,337 |
| quality_issues | 8 |
| feature_coverage | 13 |

## ML feature coverage

| feature | status | available | total | blocker |
|---|---|---|---|---|
| severity_label (Major/Moderate/Minor) | partial | 41,600 | 56,367 | Unknown pairs are NOT labels and are excluded; only 'labeled' pairs are trainable |
| negative_pairs (non-interacting drug pairs) | missing | 0 | 56,367 | Without negatives only severity classification among known interactions is valid, not interaction/no-interacti |
| ddinter_category_files | partial | 1 | 8 | Present: A. Missing files cannot be downloaded here. Build environment has no access to this source (only repo |
| interaction_text_or_mechanism | missing | 0 | 56,367 | CSV has no text; legacy JSON 'description' is a copy of the severity word |
| rxcui (RxNorm) | blocked | 0 | 1,757 | No RxNorm file in repo; raw openFDA JSON is a Git-LFS pointer. Build environment has no access to this source  |
| atc_codes | blocked | 0 | 1,757 | Not in repo; DDInter file letters (A,B,D...) are ATC top level only for the file, not a per-drug code. Build e |
| cyp450_profile (substrate/inhibitor/inducer) | blocked | 0 | 1,757 | Not in repo. Build environment has no access to this source (only repo files + PyPI reachable). |
| drugbank_id | blocked | 0 | 1,757 | Not in repo. Build environment has no access to this source (only repo files + PyPI reachable). |
| openfda_labels_with_drug_name | partial | 4,049 | 20,000 | Raw file is a Git-LFS pointer (632 MB not in repo). Names are not inferred from prose. |
| openfda_raw_parts | partial | 1 | 14 | Only part 1 (20,000 labels) is in the repo. Build environment has no access to this source (only repo files +  |
| dailymed_labels | blocked | 0 | 3,694 | Archive not in repo (only validation notes). Build environment has no access to this source (only repo files + |
| ddinter_drugs_linked_to_openfda_label_text | partial | 523 | 1,757 | Exact normalised-name match only; no fuzzy matching (would risk wrong links) |
| structured_renal_hepatic_dose_rules | missing | 0 | 0 | Label text is narrative; renal_related/hepatic_related are keyword flags only |

## ML readiness

**NOT READY for a feature-based severity model: labels exist (single DDInter category file) but there are no drug-level features (RxCUI, ATC, CYP450) and no interaction text. A drug-identity-only baseline is technically possible but would not generalise to unseen drugs; evaluate with drug-disjoint splits.**

- trainable labelled pairs: 41600 {'Major': 4906, 'Minor': 3637, 'Moderate': 33057} (majority class 79.5%)
- unusable `Unknown` pairs: 14767; conflicts: 0
- Random pair-level splits leak drug identity; use drug-disjoint (cold-start) splits.

## Issues log

| dataset | check | severity | affected | total |
|---|---|---|---|---|
| ddinter | legacy_json_description_is_severity_echo | warning | 56,367 | 56,367 |
| ddinter | normalized_name_merges_different_base_drugs | error | 1 | 1,694 |
| ddinter | route_or_salt_variants_share_normalized_name | info | 52 | 1,694 |
| openfda | labels_without_drug_name | error | 15,951 | 20,000 |
| openfda | multi_product_names | warning | 7 | 20,000 |
| openfda | names_longer_than_100_chars | warning | 34 | 20,000 |
| openfda | raw_file_is_git_lfs_pointer | error | 1 | 1 |
| dailymed | source_archive_missing | error | 3,694 | 3,694 |
