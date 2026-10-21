# severity-baseline-v2.1 - validation report

Trained 2026-10-21T11:34:14Z on **130,422** labelled DDInter pairs (1,782 drugs): {'Minor': 6833, 'Moderate': 96675, 'Major': 26914}.

**Advisory only. Not clinically validated.** Features: TF-IDF of character 3-4-grams of the normalised ingredient name + binary WHO ATC level 1-4 indicators (exact-name match; zeros when unknown); pair = [a+b, a*b]. No RxCUI/CYP450/interaction-text features (RxCUI is stored as an identifier only). The model estimates severity for an interaction that already exists; it was trained without negative examples and cannot tell whether two drugs interact. Chance macro-F1 for 3 classes is ~0.33.

## Held-out results (mean ± std over 5 seeds)

| regime | what it means | model macro-F1 | majority | random | recall Minor / Moderate / Major |
|---|---|---|---|---|---|
| `both_seen` | new pair, both drugs seen in training | 0.684 ± 0.009 | 0.284 | 0.334 | 0.87 / 0.76 / 0.85 |
| `one_unseen` | one drug never seen | 0.585 ± 0.008 | 0.284 | 0.332 | 0.64 / 0.73 / 0.66 |
| `both_unseen` | cold start: neither drug seen | 0.488 ± 0.019 | 0.285 | 0.337 | 0.44 / 0.71 / 0.48 |

## Feature comparison (same splits, same seeds)

| features | both_seen | one_unseen | both_unseen (cold start) |
|---|---|---|---|
| name n-grams only | 0.676 ± 0.009 | 0.559 ± 0.007 | 0.446 ± 0.024 |
| name n-grams + ATC levels 1-4 | 0.684 ± 0.009 | 0.585 ± 0.008 | 0.488 ± 0.019 |

Decision: **ship name + ATC (--features auto)**

## Split policy (shipped features)

DDInter lists some ingredients under several ids (route/form variants) and the model sees only the ingredient, so per-id splits leave a sibling of an 'unseen' drug in training. The reported numbers hold out whole ingredients (same RxCUI or same name together) and whole ingredient pairs.

| split | both_seen | one_unseen | both_unseen (cold start) |
|---|---|---|---|
| ingredient-grouped splits (this model) | 0.684 ± 0.009 | 0.585 ± 0.008 | 0.488 ± 0.019 |
| per-DDInter-id splits (previous reports) | 0.687 ± 0.003 | 0.590 ± 0.012 | 0.487 ± 0.024 |


## Reliability of a prediction (pooled held-out pairs)

Share of held-out pairs that truly had the predicted label, by confidence band, next to how common that label is.

### `both_seen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 1085 | 0.09 | 0.05 |
| low | Moderate | 1733 | 0.82 | 0.74 |
| low | Major | 1236 | 0.28 | 0.21 |
| medium | Minor | 3609 | 0.18 | 0.05 |
| medium | Moderate | 10630 | 0.89 | 0.74 |
| medium | Major | 6617 | 0.38 | 0.21 |
| high | Minor | 6484 | 0.46 | 0.05 |
| high | Moderate | 37039 | 0.98 | 0.74 |
| high | Major | 15804 | 0.76 | 0.21 |

### `one_unseen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 3210 | 0.11 | 0.05 |
| low | Moderate | 5187 | 0.74 | 0.74 |
| low | Major | 3648 | 0.30 | 0.20 |
| medium | Minor | 9215 | 0.16 | 0.05 |
| medium | Moderate | 30744 | 0.79 | 0.74 |
| medium | Major | 17042 | 0.36 | 0.20 |
| high | Minor | 14719 | 0.36 | 0.05 |
| high | Moderate | 89857 | 0.93 | 0.74 |
| high | Major | 31266 | 0.64 | 0.20 |

### `both_unseen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 431 | 0.10 | 0.05 |
| low | Moderate | 756 | 0.71 | 0.75 |
| low | Major | 527 | 0.26 | 0.20 |
| medium | Minor | 1144 | 0.14 | 0.05 |
| medium | Moderate | 4208 | 0.76 | 0.75 |
| medium | Major | 2218 | 0.33 | 0.20 |
| high | Minor | 1706 | 0.23 | 0.05 |
| high | Moderate | 11279 | 0.87 | 0.75 |
| high | Major | 2957 | 0.51 | 0.20 |

## Notes

- Runtime inference (`ml/inference.py`, numpy) matches scikit-learn to 6.2e-13 and is exactly symmetric in (A, B).
- The shipped weights are fit on all labelled pairs; they have no held-out set of their own.
- `both_seen` numbers reflect drug-identity signal; they say nothing about pharmacology.
- ATC codes come from an exact-name match to the WHO ATC/DDD index; drugs without a match have no ATC features.
- All 8 DDInter category files are used, but DDInter is still the only label source and has no negative pairs.
