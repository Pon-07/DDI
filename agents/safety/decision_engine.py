def evaluate_safety(interaction_result):
    if not interaction_result["found"]:
        return {
            "status": "SAFE",
            "action": "ALLOW",
            "reason": "No known drug-drug interaction found."
        }

    severity = str(interaction_result.get("severity") or "").lower()

    if severity in ["severe", "major", "high"]:
        return {
            "status": "UNSAFE",
            "action": "BLOCK",
            "reason": interaction_result.get("description"),
            "severity": severity
        }

    return {
        "status": "CAUTION",
        "action": "REVIEW",
        "reason": interaction_result.get("description"),
        "severity": severity
    }