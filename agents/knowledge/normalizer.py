def normalize_record(record, source):
    """
    Convert a dataset record into the common internal format.
    """

    normalized = {
        "source": source,
        "drug_a": None,
        "drug_b": None,
        "interaction_description": None,
        "severity": None,
        "evidence": None,
    }

    if source.lower() == "ddinter":
        normalized["drug_a"] = record.get("drug_a")
        normalized["drug_b"] = record.get("drug_b")
        normalized["interaction_description"] = record.get(
            "interaction_description"
        )
        normalized["severity"] = record.get("severity")
        normalized["evidence"] = record.get("source")

    elif source.lower() == "openfda":
        normalized["drug_a"] = record.get("brand_name")
        normalized["evidence"] = record

    return normalized


def normalize_dataset(records, source):
    """Normalize multiple records."""

    return [
        normalize_record(record, source)
        for record in records
    ]