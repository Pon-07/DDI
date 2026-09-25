from pathlib import Path
from typing import List, Optional, Set, Tuple, Union

import pandas as pd

from agents.knowledge.normalizer import DrugNormalizer
from agents.knowledge.schemas import DrugKnowledge, LabelEvidence


class OpenFDALoader:
    """
    Validated CSV Loader for OpenFDA product labeling datasets.
    Extracts raw drug entities and section-level label evidence without interpreting clinical content.
    """

    REQUIRED_COLUMNS: Set[str] = {
        "id",
        "brand_name",
        "generic_name",
        "dosage_and_administration",
        "contraindications",
        "drug_interactions",
        "warnings_and_cautions",
        "use_in_specific_populations",
        "renal_related",
        "hepatic_related",
    }

    LABEL_SECTIONS: List[str] = [
        "dosage_and_administration",
        "contraindications",
        "drug_interactions",
        "warnings_and_cautions",
        "use_in_specific_populations",
        "renal_related",
        "hepatic_related",
    ]

    def __init__(
        self,
        normalizer: Optional[DrugNormalizer] = None,
        source_name: str = "openFDA",
        source_version: Optional[str] = None,
    ):
        self.normalizer = normalizer or DrugNormalizer()
        self.source_name = source_name
        self.source_version = source_version

    def _is_valid_text(self, val: object) -> bool:
        """Check if a cell value contains non-empty string content."""
        if val is None or pd.isna(val):
            return False
        text_str = str(val).strip()
        return bool(text_str and text_str.lower() != "nan")

    def load_csv(
        self,
        file_path: Union[str, Path],
        encoding: str = "utf-8",
        **kwargs,
    ) -> Tuple[List[DrugKnowledge], List[LabelEvidence]]:
        """
        Load OpenFDA validated CSV and convert rows to DrugKnowledge and LabelEvidence representations.

        Returns:
            Tuple[List[DrugKnowledge], List[LabelEvidence]]:
                (List of extracted drug entities, List of section-level label evidence items)
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"OpenFDA dataset file not found: {path}")

        df = pd.read_csv(path, encoding=encoding, **kwargs)

        missing_cols = self.REQUIRED_COLUMNS - set(df.columns)
        if missing_cols:
            raise ValueError(
                f"OpenFDA dataset is missing required columns: {sorted(list(missing_cols))}"
            )

        drug_knowledge_list: List[DrugKnowledge] = []
        label_evidence_list: List[LabelEvidence] = []

        for _, row in df.iterrows():
            record_id_val = row["id"]
            record_id = (
                str(record_id_val).strip()
                if self._is_valid_text(record_id_val)
                else None
            )

            generic_name = (
                str(row["generic_name"]).strip()
                if self._is_valid_text(row["generic_name"])
                else ""
            )
            brand_name = (
                str(row["brand_name"]).strip()
                if self._is_valid_text(row["brand_name"])
                else ""
            )

            primary_drug_name = generic_name or brand_name
            if not primary_drug_name:
                continue

            # 1. Create DrugKnowledge entry for primary name
            normalized_primary = self.normalizer.normalize(primary_drug_name)
            drug_knowledge = DrugKnowledge(
                drug_name=primary_drug_name,
                normalized_name=normalized_primary,
                rxnorm_code=None,
                source=self.source_name,
                source_version=self.source_version,
                label_id=record_id,
            )
            drug_knowledge_list.append(drug_knowledge)

            # If brand name is distinct, add separate DrugKnowledge entry
            if brand_name and generic_name and brand_name.lower() != generic_name.lower():
                normalized_brand = self.normalizer.normalize(brand_name)
                brand_knowledge = DrugKnowledge(
                    drug_name=brand_name,
                    normalized_name=normalized_brand,
                    rxnorm_code=None,
                    source=self.source_name,
                    source_version=self.source_version,
                    label_id=record_id,
                )
                drug_knowledge_list.append(brand_knowledge)

            # 2. Extract LabelEvidence for each populated section
            for section_name in self.LABEL_SECTIONS:
                cell_value = row.get(section_name)
                if self._is_valid_text(cell_value):
                    text_content = str(cell_value).strip()
                    label_evidence = LabelEvidence(
                        drug_name=primary_drug_name,
                        section=section_name,
                        text=text_content,
                        source=self.source_name,
                        source_version=self.source_version,
                        evidence_id=record_id,
                    )
                    label_evidence_list.append(label_evidence)

        return drug_knowledge_list, label_evidence_list
