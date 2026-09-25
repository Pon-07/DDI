from .ddi_engine import check_interaction
from .decision_engine import evaluate_safety
from .resolution_engine import resolve_safety
from .audit import create_audit_record


def run_safety_check(drug_a, drug_b, ddi_records):
    interaction = check_interaction(
        drug_a,
        drug_b,
        ddi_records
    )

    decision = evaluate_safety(interaction)

    resolution = resolve_safety(decision)

    audit = create_audit_record(
        drug_a,
        drug_b,
        interaction,
        decision,
        resolution
    )

    return {
        "interaction": interaction,
        "decision": decision,
        "resolution": resolution,
        "audit": audit
    }