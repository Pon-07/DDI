# Data quality report

Pipeline v1.0.0 - generated 2026-10-23T07:11:51Z

## Production safety

Production DB files unchanged by the build: **True** (SHA-256 before/after).

## After: DDInter

| metric | value |
|---|---|
| raw rows (verbatim, all kept) | 222,383 |
| quarantined rows | 0 |
| unordered pairs | 160,235 |
| drugs (DDInter IDs) | 1,939 |
| pairs by status | {'unknown': 29813, 'labeled': 130422} |
| pairs by label | {'NULL': 29813, 'Moderate': 96675, 'Minor': 6833, 'Major': 26914} |
| reversed duplicate pairs merged | 0 |
| same-direction duplicates merged | 43,826 |
| severity conflicts (unlabelled) | 0 |
| files present / expected | A,B,D,H,L,P,R,V / A,B,D,H,L,P,R,V |
| distinct base drugs merged by normaliser | 3 |


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

DailyMed labels ingested: **0** (missing). DDInter-to-openFDA exact-name links: 2537.

## Staging table row counts

| table | rows |
|---|---|
| source_files | 29 |
| ddinter_raw | 222,383 |
| ddinter_drugs | 1,939 |
| ddinter_pairs | 160,235 |
| ddinter_pair_rows | 222,383 |
| openfda_labels | 20,000 |
| openfda_sections | 37,197 |
| dailymed_labels | 0 |
| dailymed_sections | 0 |
| drug_xref | 2,537 |
| drug_rxcui | 1,744 |
| quality_issues | 9 |
| feature_coverage | 12 |

## ML feature coverage

| feature | status | available | total | blocker |
|---|---:|---:|---:|---|
| severity_label (Major/Moderate/Minor) | partial | 130422 | 160235 | Unknown pairs are NOT labels and are excluded; only 'labeled' pairs are trainable |
| negative_pairs (non-interacting drug pairs) | missing | 0 | 160235 | Without negatives only severity classification among known interactions is valid, not interaction/no-interaction prediction |
| ddinter_category_files | available | 8 | 8 | Present: A,B,D,H,L,P,R,V. Missing files cannot be downloaded here. Build environment has no access to this source (only repo files + PyPI reachable). |
| interaction_text_or_mechanism | missing | 0 | 160235 | CSV has no text; legacy JSON 'description' is a copy of the severity word |
| rxcui (RxNorm) | partial | 1744 | 1939 | Exact normalised-name match to RxNorm ingredient concepts (IN/PIN); ambiguous names skipped; ClinPGx fills a few more. Unmatched drugs stay NULL. Identifier only: it is not used as a model feature yet. |
| cyp450_profile (substrate/inhibitor/inducer) | blocked | 0 | 1939 | Not in repo. Build environment has no access to this source (only repo files + PyPI reachable). |
| drugbank_id | blocked | 0 | 1939 | Not in repo. Build environment has no access to this source (only repo files + PyPI reachable). |
| openfda_labels_with_drug_name | partial | 4049 | 20000 | Raw file is a Git-LFS pointer (632 MB not in repo). Names are not inferred from prose. |
| openfda_raw_parts | partial | 1 | 14 | Only part 1 (20,000 labels) is in the repo. Build environment has no access to this source (only repo files + PyPI reachable). |
| dailymed_labels | blocked | 0 | 3694 | Archive not in repo (only validation notes). Build environment has no access to this source (only repo files + PyPI reachable). |
| ddinter_drugs_linked_to_openfda_label_text | partial | 550 | 1939 | Exact normalised-name match only; no fuzzy matching (would risk wrong links) |
| structured_renal_hepatic_dose_rules | missing | 0 | 0 | Label text is narrative; renal_related/hepatic_related are keyword flags only |

## ML readiness

Public candidate features use drug-name text only; WHO-derived ATC data and coverage metrics are withheld pending licensing review.


- trainable labelled pairs: 130422 {'Major': 26914, 'Minor': 6833, 'Moderate': 96675} (majority class 74.1%)
- unusable `Unknown` pairs: 29813; conflicts: 0
- Random pair-level splits leak drug identity; use drug-disjoint (cold-start) splits.

## Issues log

| dataset | check | severity | affected | total |
|---|---|---|---|---|
| ddinter | same_direction_duplicate_pairs_merged | info | 43,826 | 160,235 |
| ddinter | legacy_json_description_is_severity_echo | warning | 56,367 | 56,367 |
| ddinter | normalized_name_merges_different_base_drugs | error | 3 | 1,853 |
| ddinter | route_or_salt_variants_share_normalized_name | info | 72 | 1,853 |
| openfda | labels_without_drug_name | error | 15,951 | 20,000 |
| openfda | multi_product_names | warning | 7 | 20,000 |
| openfda | names_longer_than_100_chars | warning | 34 | 20,000 |
| openfda | raw_file_is_git_lfs_pointer | error | 1 | 1 |
| dailymed | source_archive_missing | error | 3,694 | 3,694 |
