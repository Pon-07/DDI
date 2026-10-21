# severity-baseline-v2 - validation report

Trained 2026-10-21T01:52:37Z on **130,422** labelled DDInter pairs (1,782 drugs): {'Minor': 6833, 'Moderate': 96675, 'Major': 26914}.

**Advisory only. Not clinically validated.** Features: TF-IDF of character 3-4-grams of the normalised ingredient name + binary WHO ATC level 1-4 indicators (exact-name match; zeros when unknown); pair = [a+b, a*b]. No RxCUI/CYP450/interaction text. The model estimates severity for an interaction that already exists; it was trained without negative examples and cannot tell whether two drugs interact. Chance macro-F1 for 3 classes is ~0.33.

## Held-out results (mean ± std over 5 seeds)

| regime | what it means | model macro-F1 | majority | random | recall Minor / Moderate / Major |
|---|---|---|---|---|---|
| `both_seen` | new pair, both drugs seen in training | 0.684 ± 0.009 | 0.284 | 0.330 | 0.89 / 0.76 / 0.84 |
| `one_unseen` | one drug never seen | 0.589 ± 0.011 | 0.285 | 0.333 | 0.60 / 0.74 / 0.67 |
| `both_unseen` | cold start: neither drug seen | 0.485 ± 0.024 | 0.287 | 0.334 | 0.35 / 0.73 / 0.49 |

## Feature comparison (same splits, same seeds)

| features | both_seen | one_unseen | both_unseen (cold start) |
|---|---|---|---|
| name n-grams only | 0.674 ± 0.009 | 0.565 ± 0.010 | 0.447 ± 0.023 |
| name n-grams + ATC levels 1-4 | 0.684 ± 0.009 | 0.589 ± 0.011 | 0.485 ± 0.024 |

Decision: **ship name + ATC (--features auto)**


## Reliability of a prediction (pooled held-out pairs)

Share of held-out pairs that truly had the predicted label, by confidence band, next to how common that label is.

### `both_seen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 1058 | 0.09 | 0.05 |
| low | Moderate | 1594 | 0.80 | 0.74 |
| low | Major | 1178 | 0.26 | 0.21 |
| medium | Minor | 3658 | 0.20 | 0.05 |
| medium | Moderate | 10589 | 0.88 | 0.74 |
| medium | Major | 6613 | 0.37 | 0.21 |
| high | Minor | 6600 | 0.45 | 0.05 |
| high | Moderate | 37054 | 0.98 | 0.74 |
| high | Major | 15810 | 0.76 | 0.21 |

### `one_unseen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 2681 | 0.10 | 0.05 |
| low | Moderate | 4599 | 0.73 | 0.75 |
| low | Major | 3242 | 0.28 | 0.20 |
| medium | Minor | 8255 | 0.18 | 0.05 |
| medium | Moderate | 29618 | 0.80 | 0.75 |
| medium | Major | 16876 | 0.35 | 0.20 |
| high | Minor | 14031 | 0.36 | 0.05 |
| high | Moderate | 94236 | 0.92 | 0.75 |
| high | Major | 32661 | 0.63 | 0.20 |

### `both_unseen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 333 | 0.12 | 0.06 |
| low | Moderate | 630 | 0.68 | 0.76 |
| low | Major | 428 | 0.24 | 0.19 |
| medium | Minor | 948 | 0.15 | 0.06 |
| medium | Moderate | 3897 | 0.76 | 0.76 |
| medium | Major | 2158 | 0.32 | 0.19 |
| high | Minor | 1460 | 0.23 | 0.06 |
| high | Moderate | 12021 | 0.87 | 0.76 |
| high | Major | 3250 | 0.46 | 0.19 |

## Notes

- Runtime inference (`ml/inference.py`, numpy) matches scikit-learn to 5.8e-13 and is exactly symmetric in (A, B).
- The shipped weights are fit on all labelled pairs; they have no held-out set of their own.
- `both_seen` numbers reflect drug-identity signal; they say nothing about pharmacology.
- ATC codes come from an exact-name match to the WHO ATC/DDD index; drugs without a match have no ATC features.
- All 8 DDInter category files are used, but DDInter is still the only label source and has no negative pairs.
