# AEGIS Rx - Clinical Safety and Drug-Drug Interaction Resolution Platform

## Alternative ranker

gents/resolution/alternatives.py proposes replacement drugs for a flagged drug, ranked by a transparent weighted score (interaction risk 0.60, ATC-class closeness 0.25, dosing-evidence availability 0.15; weights and penalties live in RANKER_CONFIG). There is no LLM and no trained model in the decision. It is optional: SafetyResolutionEngine(alternative_ranker=AlternativeRanker()); by default nothing changes. Results appear as esult.alternatives and every item carries equires_cosign=True.

- **Risk gate:** full ranked alternatives only for HIGH risk (finding severity critical/contraindicated/major/review_required/high, or a DDInter known Major for the flagged pair). MODERATE gives a monitoring action plus an optional secondary list of at most 3. LOW or undetermined gives no alternatives and says why. Duplicate therapy returns a "consolidate duplicate therapy" action instead of substitutes.
- **Candidates:** same ATC level-4 class (level 3 is added only if fewer than 3 level-4 candidates are free of a known Major; level 4 always ranks first). Active drugs, the flagged drug, identical RxCUI/ingredient and combination products are excluded. Each candidate is checked against every other active drug.
- **Order:** known Major last (and flagged); then level 4 before 3; then known Minor, known Moderate, unknown, ML-raised caution; then score; then name.
- **ML advisory:** for pairs DDInter does not label, the existing severity advisor's estimate is displayed with probabilities, confidence band and regime. It can only raise caution (a non-low-confidence Major lowers rank and flags pharmacist review). Minor/Moderate estimates never improve rank or make a pair "safe"; low-confidence estimates are shown as unreliable and change nothing. If the artifact is missing the ranker works without it.
- **Coverage:** python scripts/alternative_ranker_coverage.py regenerates eports/alternative_ranker/coverage.md (aggregate counts over the DDInter Major pairs; no drug names or ATC codes).

Limits: ATC class is not clinical equivalence (route, form, indication, potency and patient suitability are not checked). A pair absent from DDInter is unknown, not safe. The dosing component only says whether label evidence exists (keyword flags from the openFDA clean data); it never proposes or derives a dose. The ranker has not been clinically validated, and a pharmacist must review every suggestion. It reads the clean CSVs at runtime and does not copy ATC data anywhere else.
