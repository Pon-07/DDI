import json
import csv
from pathlib import Path


def load_json(file_path):
    """Load a JSON dataset."""
    path = Path(file_path)

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_csv(file_path):
    """Load a CSV dataset."""
    path = Path(file_path)

    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader)


def load_dataset(file_path):
    """Load a dataset based on its file extension."""
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {file_path}")

    if path.suffix.lower() == ".json":
        return load_json(path)

    if path.suffix.lower() == ".csv":
        return load_csv(path)

    raise ValueError(f"Unsupported dataset format: {path.suffix}")