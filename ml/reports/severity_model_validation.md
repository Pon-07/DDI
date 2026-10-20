# severity-baseline-v1 - validation report

Trained 2026-10-20T03:41:31Z on **41,600** labelled DDInter pairs (1,610 drugs): {'Minor': 3637, 'Moderate': 33057, 'Major': 4906}.

**Advisory only. Not clinically validated.** Features are drug-name text only (no RxCUI/ATC/CYP450/interaction text). The model estimates severity for an interaction that already exists; it was trained without negative examples and cannot tell whether two drugs interact. Chance macro-F1 for 3 classes is ~0.33.

## Held-out results (mean ± std over 5 seeds)

| regime | what it means | model macro-F1 | majority | random | recall Minor / Moderate / Major |
|---|---|---|---|---|---|
| `both_seen` | new pair, both drugs seen in training | 0.685 ± 0.011 | 0.294 | 0.332 | 0.83 / 0.77 / 0.85 |
| `one_unseen` | one drug never seen | 0.581 ± 0.008 | 0.296 | 0.335 | 0.61 / 0.74 / 0.66 |
| `both_unseen` | cold start: neither drug seen | 0.445 ± 0.018 | 0.297 | 0.327 | 0.34 / 0.74 / 0.36 |

## Reliability of a prediction (pooled held-out pairs)

Share of held-out pairs that truly had the predicted label, by confidence band, next to how common that label is.

### `both_seen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 476 | 0.12 | 0.09 |
| low | Moderate | 875 | 0.84 | 0.79 |
| low | Major | 515 | 0.15 | 0.12 |
| medium | Minor | 1499 | 0.27 | 0.09 |
| medium | Moderate | 3976 | 0.93 | 0.79 |
| medium | Major | 1761 | 0.30 | 0.12 |
| high | Minor | 2392 | 0.61 | 0.09 |
| high | Moderate | 12148 | 0.99 | 0.79 |
| high | Major | 3488 | 0.64 | 0.12 |

### `one_unseen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 1283 | 0.16 | 0.09 |
| low | Moderate | 2192 | 0.79 | 0.80 |
| low | Major | 1291 | 0.19 | 0.11 |
| medium | Minor | 3749 | 0.23 | 0.09 |
| medium | Moderate | 11122 | 0.85 | 0.80 |
| medium | Major | 4692 | 0.30 | 0.11 |
| high | Minor | 5109 | 0.48 | 0.09 |
| high | Moderate | 28905 | 0.94 | 0.80 |
| high | Major | 6324 | 0.49 | 0.11 |

### `both_unseen`

| band | predicted | n | precision | base rate |
|---|---|---|---|---|
| low | Minor | 166 | 0.16 | 0.09 |
| low | Moderate | 330 | 0.72 | 0.80 |
| low | Major | 151 | 0.09 | 0.10 |
| medium | Minor | 430 | 0.18 | 0.09 |
| medium | Moderate | 1596 | 0.80 | 0.80 |
| medium | Major | 534 | 0.24 | 0.10 |
| high | Minor | 504 | 0.29 | 0.09 |
| high | Moderate | 3467 | 0.88 | 0.80 |
| high | Major | 495 | 0.30 | 0.10 |

## Notes

- Runtime inference (`ml/inference.py`, numpy) matches scikit-learn to 6.7e-13 and is exactly symmetric in (A, B).
- The shipped weights are fit on all labelled pairs; they have no held-out set of their own.
- `both_seen` numbers reflect drug-identity signal; they say nothing about pharmacology.
- Only DDInter category A is available (1 of 8 files), so coverage of drug classes is partial.
