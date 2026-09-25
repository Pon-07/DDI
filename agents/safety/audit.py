from datetime import datetime


def create_audit_record(drug_a, drug_b, interaction, decision, resolution):
    return {
        "timestamp": datetime.now().isoformat(),
        "drug_a": drug_a,
        "drug_b": drug_b,
        "interaction_found": interaction.get("found"),
        "severity": interaction.get("severity"),
        "decision": decision.get("status"),
        "action": decision.get("action"),
        "resolution": resolution.get("resolution"),
        "reason": decision.get("reason"),
    }