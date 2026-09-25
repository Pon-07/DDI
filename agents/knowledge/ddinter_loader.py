from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import pandas as pd

from agents.knowledge.normalizer import DrugNormalizer
from agents.knowledge.schemas import InteractionEvidence


class DDInterLoader:
    """
    Validated CSV Loader for DDInter drug-drug interaction datasets.
    Dynamically maps and validates input columns without hardcoding clinical assumptions.
    """

    DRUG_A_CANDIDATES: Set[str] = {
        "drug_a",
        "druga",
        "drug1",
        "drug_1",
        "ddinter_drug_a",
        "first_drug",
        "item_a",
    }
    DRUG_B_CANDIDATES: Set[str] = {
        "drug_b",
        "drugb",
        "drug2",
        "drug_2",
        "ddinter_drug_b",
        "second_drug",
        "item_b",
    }
    DESCRIPTION_CANDIDATES: Set[str] = {
        "description",
        "mechanism",
        "interaction_detail",
        "actions",
        "action",
        "effect",
        "evidence_text",
    }
    ID_CANDIDATES: Set[str] = {
        "id",
        "evidence_id",
        "ddinter_id",
        "ddinterid",
        "interaction_id",
        "record_id",
    }
    LEVEL_CANDIDATES: Set[str] = {
        "level",
        "severity",
        "risk_level",
        "interaction_level",
    }

    def __init__(
        self,
        normalizer: Optional[DrugNormalizer] = None,
        source_name: str = "DDInter",
        source_version: Optional[str] = None,
    ):
        self.normalizer = normalizer or DrugNormalizer()
        self.source_name = source_name
        self.source_version = source_version

    def _normalize_col_name(self, col: str) -> str:
        """Normalize column name for robust comparison."""
        return str(col).lower().strip().replace(" ", "_").replace("-", "_")

    def resolve_columns(self, columns: List[str]) -> Dict[str, Optional[str]]:
        """
        Dynamically map available DataFrame columns to interaction fields.
        """
        normalized_to_actual = {self._normalize_col_name(c): c for c in columns}

        resolved: Dict[str, Optional[str]] = {
            "drug_a": None,
            "drug_b": None,
            "description": None,
            "id": None,
            "level": None,
            "id_a": None,
            "id_b": None,
        }

        for norm_col, actual_col in normalized_to_actual.items():
            if norm_col in self.DRUG_A_CANDIDATES and not resolved["drug_a"]:
                resolved["drug_a"] = actual_col
            elif norm_col in self.DRUG_B_CANDIDATES and not resolved["drug_b"]:
                resolved["drug_b"] = actual_col
            elif norm_col in self.DESCRIPTION_CANDIDATES and not resolved["description"]:
                resolved["description"] = actual_col
            elif norm_col in self.ID_CANDIDATES and not resolved["id"]:
                resolved["id"] = actual_col
            elif norm_col in self.LEVEL_CANDIDATES and not resolved["level"]:
                resolved["level"] = actual_col
            elif norm_col in {"ddinterid_a", "id_a", "drug_a_id"} and not resolved["id_a"]:
                resolved["id_a"] = actual_col
            elif norm_col in {"ddinterid_b", "id_b", "drug_b_id"} and not resolved["id_b"]:
                resolved["id_b"] = actual_col

        if not resolved["drug_a"] or not resolved["drug_b"]:
            missing = []
            if not resolved["drug_a"]:
                missing.append("drug_a")
            if not resolved["drug_b"]:
                missing.append("drug_b")
            raise ValueError(
                f"DDInter dataset missing essential drug column(s): {missing}. Available columns: {columns}"
            )

        return resolved

    def _is_valid_text(self, val: object) -> bool:
        if val is None or pd.isna(val):
            return False
        text_str = str(val).strip()
        return bool(text_str and text_str.lower() != "nan")

    def load_csv(
        self,
        file_path: Union[str, Path],
        delimiter: str = ",",
        encoding: str = "utf-8",
        **kwargs,
    ) -> Tuple[List[InteractionEvidence], List[Dict[str, Any]]]:
        """
        Load and parse DDInter CSV interaction records.

        Returns:
            Tuple[List[InteractionEvidence], List[Dict[str, Any]]]:
                (List of valid InteractionEvidence objects, List of validation error records)
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"DDInter dataset file not found: {path}")

        df = pd.read_csv(path, delimiter=delimiter, encoding=encoding, **kwargs)

        col_map = self.resolve_columns(list(df.columns))

        evidences: List[InteractionEvidence] = []
        validation_errors: List[Dict[str, Any]] = []

        drug_a_col = col_map["drug_a"]
        drug_b_col = col_map["drug_b"]
        desc_col = col_map["description"]
        id_col = col_map["id"]
        id_a_col = col_map["id_a"]
        id_b_col = col_map["id_b"]
        level_col = col_map["level"]

        for idx, row in df.iterrows():
            raw_a = row.get(drug_a_col)
            raw_b = row.get(drug_b_col)

            if not self._is_valid_text(raw_a) or not self._is_valid_text(raw_b):
                validation_errors.append(
                    {
                        "row_index": idx,
                        "reason": "Missing or blank drug name",
                        "row_data": dict(row),
                    }
                )
                continue

            drug_a_str = str(raw_a).strip()
            drug_b_str = str(raw_b).strip()

            norm_a = self.normalizer.normalize(drug_a_str)
            norm_b = self.normalizer.normalize(drug_b_str)

            if not norm_a or not norm_b:
                validation_errors.append(
                    {
                        "row_index": idx,
                        "reason": "Drug name normalization produced empty string",
                        "row_data": dict(row),
                    }
                )
                continue

            # Determine description
            description_text = ""
            if desc_col and self._is_valid_text(row.get(desc_col)):
                description_text = str(row.get(desc_col)).strip()
            elif level_col and self._is_valid_text(row.get(level_col)):
                description_text = (
                    f"Reported DDInter interaction with risk level: {row.get(level_col)}"
                )
            else:
                description_text = (
                    f"Reported DDInter interaction between {drug_a_str} and {drug_b_str}."
                )

            # Determine evidence_id
            evidence_id = None
            if id_col and self._is_valid_text(row.get(id_col)):
                evidence_id = str(row.get(id_col)).strip()
            elif (
                id_a_col
                and id_b_col
                and self._is_valid_text(row.get(id_a_col))
                and self._is_valid_text(row.get(id_b_col))
            ):
                evidence_id = f"{row.get(id_a_col)}_{row.get(id_b_col)}"

            evidence = InteractionEvidence(
                drug_a=norm_a,
                drug_b=norm_b,
                description=description_text,
                source=self.source_name,
                source_version=self.source_version,
                evidence_id=evidence_id,
            )
            evidences.append(evidence)

        return evidences, validation_errors
