def check_interaction(drug_a, drug_b, ddi_records):
    for record in ddi_records:
        a = str(record.get("drug_a", "")).lower()
        b = str(record.get("drug_b", "")).lower()

        if (a == drug_a.lower() and b == drug_b.lower()) or \
           (a == drug_b.lower() and b == drug_a.lower()):
            return {
                "found": True,
                "severity": record.get("severity"),
                "description": record.get("interaction_description"),
                "source": record.get("source")
            }

    return {
        "found": False,
        "severity": None,
        "description": None,
        "source": None
    }