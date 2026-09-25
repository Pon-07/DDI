from agents.knowledge.loader import load_dataset
from agents.knowledge.normalizer import normalize_dataset


records = [
    {
        "drug_a": "DrugA",
        "drug_b": "DrugB",
        "interaction_description": "Test interaction",
        "severity": "moderate",
        "source": "DDInter"
    }
]

normalized = normalize_dataset(records, "ddinter")

print("Knowledge Agent test: OK")
print(normalized)