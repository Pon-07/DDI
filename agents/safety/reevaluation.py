from .decision_engine import evaluate_safety


def reevaluate_medications(medications, ddi_records):
    results = []

    for i, drug_a in enumerate(medications):
        for drug_b in medications[i + 1:]:
            from .ddi_engine import check_interaction

            interaction = check_interaction(
                drug_a,
                drug_b,
                ddi_records
            )

            decision = evaluate_safety(interaction)

            results.append({
                "drug_a": drug_a,
                "drug_b": drug_b,
                "interaction": interaction,
                "decision": decision
            })

    return results