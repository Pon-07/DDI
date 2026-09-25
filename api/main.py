from fastapi import FastAPI
from pydantic import BaseModel

from agents.knowledge.loader import load_dataset
from agents.knowledge.normalizer import normalize_dataset
from agents.safety.workflow import run_safety_check


app = FastAPI(title="Medication Safety Platform")

DDINTER_FILE = r"data_validation\ddinter_normalized.json"

ddi_records = []


class MedicationRequest(BaseModel):
    drug_a: str
    drug_b: str


def load_knowledge():
    global ddi_records

    data = load_dataset(DDINTER_FILE)
    ddi_records = normalize_dataset(data, "ddinter")

    return len(ddi_records)


load_knowledge()


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Medication Safety Platform",
        "knowledge_records": len(ddi_records)
    }


@app.post("/safety/check")
def safety_check(request: MedicationRequest):
    return run_safety_check(
        request.drug_a,
        request.drug_b,
        ddi_records
    )