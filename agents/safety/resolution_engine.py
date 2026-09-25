def resolve_safety(safety_result):
    if safety_result["status"] == "SAFE":
        return {
            "resolution": "KEEP",
            "message": "Medication combination can proceed."
        }

    if safety_result["status"] == "UNSAFE":
        return {
            "resolution": "BLOCK",
            "message": "Medication combination should be blocked and clinically reviewed."
        }

    return {
        "resolution": "REVIEW",
        "message": "Medication combination requires clinical review."
    }