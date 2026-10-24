# Baseline severity experiment (no model deployed)

Labelled pairs: **41,600** {'Moderate': 33057, 'Major': 4906, 'Minor': 3637} | drugs: 1,706 | seeds: 5

Only DDInter category A is available; features are drug-name character n-grams and openFDA keyword flags. There is no RxCUI/ATC/CYP450/interaction text. Chance for macro-F1 on 3 classes is ~0.33.

## Split: `random_pairs`
train/test pairs (seed 0): 33,280 / 8,320

| model | accuracy | macro-F1 | balanced acc | recall Minor | recall Moderate | recall Major |
|---|---|---|---|---|---|---|
| majority_class | 0.793 ± 0.004 | 0.295 ± 0.001 | 0.333 | 0.00 | 1.00 | 0.00 |
| stratified_random_guess | 0.652 ± 0.004 | 0.334 ± 0.002 | 0.334 | 0.09 | 0.80 | 0.12 |
| logreg_balanced[name_ngrams+label_flags] | 0.790 ± 0.003 | 0.697 ± 0.005 | 0.829 | 0.85 | 0.77 | 0.86 |
| logreg_balanced[name_ngrams_only] | 0.782 ± 0.003 | 0.689 ± 0.005 | 0.825 | 0.85 | 0.76 | 0.86 |
| logreg_balanced[label_flags_only] | 0.161 ± 0.004 | 0.169 ± 0.004 | 0.415 | 0.50 | 0.04 | 0.70 |

## Split: `drug_disjoint`
train/test pairs (seed 0): 26,578 / 1,756

| model | accuracy | macro-F1 | balanced acc | recall Minor | recall Moderate | recall Major |
|---|---|---|---|---|---|---|
| majority_class | 0.802 ± 0.025 | 0.297 ± 0.005 | 0.333 | 0.00 | 1.00 | 0.00 |
| stratified_random_guess | 0.645 ± 0.012 | 0.330 ± 0.015 | 0.332 | 0.09 | 0.78 | 0.13 |
| logreg_balanced[name_ngrams+label_flags] | 0.665 ± 0.024 | 0.455 ± 0.014 | 0.498 | 0.34 | 0.73 | 0.42 |
| logreg_balanced[name_ngrams_only] | 0.668 ± 0.030 | 0.448 ± 0.012 | 0.482 | 0.32 | 0.75 | 0.38 |
| logreg_balanced[label_flags_only] | 0.258 ± 0.095 | 0.226 ± 0.056 | 0.405 | 0.43 | 0.20 | 0.59 |

## How to read this

- `random_pairs` lets the same drug appear in train and test, so it overstates how well the model generalises. Do not quote it as performance.
- `drug_disjoint` is the honest number: test drugs are never seen in training (new-drug / cold-start).
- Accuracy is misleading here because the majority class is ~79.5% Moderate; judge by macro-F1 and per-class recall.
