# severity-baseline-v2.1 - validation report

Trained 2026-10-25T06:01:19Z on **130,422** labelled DDInter pairs (1,782 drugs): {'Minor': 6833, 'Moderate': 96675, 'Major': 26914}.

**Advisory only. Not clinically validated.** Features: TF-IDF of character 3-4-grams of the normalised ingredient name; pair = [a+b, a*b]. No RxCUI/CYP450/interaction-text features (RxCUI is stored as an identifier only). The model estimates severity for an interaction that already exists; it was trained without negative examples and cannot tell whether two drugs interact. Chance macro-F1 for 3 classes is ~0.33.

## Held-out results (mean ± std over 5 seeds)

| regime | what it means | model macro-F1 | majority | random | recall Minor / Moderate / Major |
|---|---|---|---|---|---|
| `both_seen` | new pair, both drugs seen in training | 0.673 ± 0.009 | 0.284 | 0.332 | 0.87 / 0.75 / 0.83 |
| `one_unseen` | one drug never seen | 0.554 ± 0.012 | 0.284 | 0.335 | 0.61 / 0.70 / 0.63 |
| `both_unseen` | cold start: neither drug seen | 0.430 ± 0.020 | 0.285 | 0.330 | 0.38 / 0.66 / 0.41 |

## Feature comparison (same splits, same seeds)

| features | both_seen | one_unseen | both_unseen (cold start) |
|---|---|---|---|
| name n-grams only | 0.673 ± 0.009 | 0.554 ± 0.012 | 0.430 ± 0.020 |

Decision: **ship name-only (--features name)**

## Split policy (shipped features)

DDInter lists some ingredients under several ids (route/form variants) and the model sees only the ingredient, so per-id splits leave a sibling of an 'unseen' drug in training. The reported numbers hold out whole ingredients (same RxCUI or same name together) and whole ingredient pairs.

| split | both_seen | one_unseen | both_unseen (cold start) |
|---|---|---|---|
| ingredient-grouped splits (this model) | 0.673 ± 0.009 | 0.554 ± 0.012 | 0.430 ± 0.020 |
| per-DDInter-id splits (previous reports) | 0.678 ± 0.004 | 0.565 ± 0.009 | 0.448 ± 0.021 |


## Reliability of a prediction (pooled held-out pairs)

Share of held-out pairs that truly had the predicted label, by confidence band, next to how common that label is.

### `both_seen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 1268 | 0.11 | 0.05 |
| low | Moderate | 2120 | 0.83 | 0.74 |
| low | Major | 1416 | 0.24 | 0.21 |
| medium | Minor | 4108 | 0.20 | 0.05 |
| medium | Moderate | 11783 | 0.89 | 0.74 |
| medium | Major | 6609 | 0.39 | 0.21 |
| high | Minor | 6399 | 0.44 | 0.05 |
| high | Moderate | 34155 | 0.98 | 0.74 |
| high | Major | 14906 | 0.76 | 0.21 |

### `one_unseen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 4013 | 0.09 | 0.05 |
| low | Moderate | 7261 | 0.77 | 0.74 |
| low | Major | 4647 | 0.27 | 0.20 |
| medium | Minor | 11185 | 0.14 | 0.05 |
| medium | Moderate | 35825 | 0.80 | 0.74 |
| medium | Major | 19283 | 0.38 | 0.20 |
| high | Minor | 15732 | 0.30 | 0.05 |
| high | Moderate | 83327 | 0.91 | 0.74 |
| high | Major | 29886 | 0.62 | 0.20 |

### `both_unseen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 553 | 0.06 | 0.05 |
| low | Moderate | 1062 | 0.74 | 0.75 |
| low | Major | 724 | 0.27 | 0.20 |
| medium | Minor | 1597 | 0.09 | 0.05 |
| medium | Moderate | 4985 | 0.77 | 0.75 |
| medium | Major | 2695 | 0.32 | 0.20 |
| high | Minor | 1935 | 0.16 | 0.05 |
| high | Moderate | 10435 | 0.83 | 0.75 |
| high | Major | 2673 | 0.44 | 0.20 |

## Notes

- Runtime inference (`ml/inference.py`, numpy) matches scikit-learn to 4.9e-13 and is exactly symmetric in (A, B).
- The shipped weights are fit on all labelled pairs; they have no held-out set of their own.
- `both_seen` numbers reflect drug-identity signal; they say nothing about pharmacology.
- This public artifact uses drug-name text only and contains no WHO-derived data (no ATC codes, tokens or lookups).
- All 8 DDInter category files are used, but DDInter is still the only label source and has no negative pairs.
