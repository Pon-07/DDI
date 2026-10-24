# severity-baseline-v2.1 - validation report

Trained 2026-10-23T07:13:41Z on **130,422** labelled DDInter pairs (1,782 drugs): {'Minor': 6833, 'Moderate': 96675, 'Major': 26914}.

**Advisory only. Not clinically validated.** Features: TF-IDF of character 3-4-grams of the normalised ingredient name; pair = [a+b, a*b]. No RxCUI/CYP450/interaction-text features (RxCUI is stored as an identifier only). The model estimates severity for an interaction that already exists; it was trained without negative examples and cannot tell whether two drugs interact. Chance macro-F1 for 3 classes is ~0.33.

## Held-out results (mean ± std over 5 seeds)

| regime | what it means | model macro-F1 | majority | random | recall Minor / Moderate / Major |
|---|---|---|---|---|---|
| `both_seen` | new pair, both drugs seen in training | 0.673 ± 0.009 | 0.284 | 0.332 | 0.87 / 0.75 / 0.83 |
| `one_unseen` | one drug never seen | 0.554 ± 0.011 | 0.284 | 0.335 | 0.61 / 0.70 / 0.63 |
| `both_unseen` | cold start: neither drug seen | 0.429 ± 0.016 | 0.285 | 0.330 | 0.37 / 0.66 / 0.41 |

## Feature comparison (same splits, same seeds)

| features | both_seen | one_unseen | both_unseen (cold start) |
|---|---|---|---|
| name n-grams only | 0.673 ± 0.009 | 0.554 ± 0.011 | 0.429 ± 0.016 |

Decision: **ship name-only (--features name)**

## Split policy (shipped features)

DDInter lists some ingredients under several ids (route/form variants) and the model sees only the ingredient, so per-id splits leave a sibling of an 'unseen' drug in training. The reported numbers hold out whole ingredients (same RxCUI or same name together) and whole ingredient pairs.

| split | both_seen | one_unseen | both_unseen (cold start) |
|---|---|---|---|
| ingredient-grouped splits (this model) | 0.673 ± 0.009 | 0.554 ± 0.011 | 0.429 ± 0.016 |
| per-DDInter-id splits (previous reports) | 0.677 ± 0.004 | 0.566 ± 0.008 | 0.445 ± 0.020 |


## Reliability of a prediction (pooled held-out pairs)

Share of held-out pairs that truly had the predicted label, by confidence band, next to how common that label is.

### `both_seen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 1231 | 0.10 | 0.05 |
| low | Moderate | 2098 | 0.83 | 0.74 |
| low | Major | 1391 | 0.26 | 0.21 |
| medium | Minor | 4130 | 0.20 | 0.05 |
| medium | Moderate | 11724 | 0.89 | 0.74 |
| medium | Major | 6578 | 0.38 | 0.21 |
| high | Minor | 6386 | 0.44 | 0.05 |
| high | Moderate | 34297 | 0.98 | 0.74 |
| high | Major | 14929 | 0.76 | 0.21 |

### `one_unseen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 3984 | 0.09 | 0.05 |
| low | Moderate | 7221 | 0.77 | 0.74 |
| low | Major | 4581 | 0.28 | 0.20 |
| medium | Minor | 11078 | 0.14 | 0.05 |
| medium | Moderate | 35578 | 0.80 | 0.74 |
| medium | Major | 19105 | 0.38 | 0.20 |
| high | Minor | 15637 | 0.30 | 0.05 |
| high | Moderate | 83776 | 0.91 | 0.74 |
| high | Major | 30199 | 0.62 | 0.20 |

### `both_unseen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 560 | 0.08 | 0.05 |
| low | Moderate | 1084 | 0.75 | 0.75 |
| low | Major | 735 | 0.25 | 0.20 |
| medium | Minor | 1581 | 0.08 | 0.05 |
| medium | Moderate | 4999 | 0.76 | 0.75 |
| medium | Major | 2652 | 0.32 | 0.20 |
| high | Minor | 1895 | 0.17 | 0.05 |
| high | Moderate | 10437 | 0.83 | 0.75 |
| high | Major | 2716 | 0.44 | 0.20 |

## Notes

- Runtime inference (`ml/inference.py`, numpy) matches scikit-learn to 7.1e-13 and is exactly symmetric in (A, B).
- The shipped weights are fit on all labelled pairs; they have no held-out set of their own.
- `both_seen` numbers reflect drug-identity signal; they say nothing about pharmacology.
- This public artifact uses drug-name text only and contains no WHO-derived data (no ATC codes, tokens or lookups).
- All 8 DDInter category files are used, but DDInter is still the only label source and has no negative pairs.
