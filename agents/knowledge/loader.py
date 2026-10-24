import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from agents.knowledge.normalizer import DrugNormalizer


@dataclass
class KnowledgeDataset:
    """Encapsulates a loaded medication knowledge dataset with metadata and records."""

    source_name: str
    records: List[Dict[str, Any]] = field(default_factory=list)
    dataframe: Optional[pd.DataFrame] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        if self.dataframe is not None:
            return len(self.dataframe)
        return len(self.records)

    def to_dataframe(self) -> pd.DataFrame:
        if self.dataframe is not None:
            return self.dataframe
        return pd.DataFrame(self.records)


class BaseKnowledgeLoader(ABC):
    """Abstract base class for all medication knowledge data loaders."""

    def __init__(self, normalizer: Optional[DrugNormalizer] = None):
        self.normalizer = normalizer or DrugNormalizer()

    @abstractmethod
    def load(
        self,
        source: Union[str, Path],
        **kwargs: Any,
    ) -> KnowledgeDataset:
        """Load data from a file or data source."""
        pass


class CSVKnowledgeLoader(BaseKnowledgeLoader):
    """Generic CSV Knowledge dataset loader supporting column mapping and normalization."""

    def __init__(
        self,
        source_name: str = "csv_dataset",
        column_mapping: Optional[Dict[str, str]] = None,
        normalize_columns: Optional[List[str]] = None,
        normalizer: Optional[DrugNormalizer] = None,
    ):
        super().__init__(normalizer=normalizer)
        self.source_name = source_name
        self.column_mapping = column_mapping or {}
        self.normalize_columns = normalize_columns or []

    def load(
        self,
        source: Union[str, Path],
        delimiter: str = ",",
        encoding: str = "utf-8",
        **kwargs: Any,
    ) -> KnowledgeDataset:
        file_path = Path(source)
        if not file_path.exists():
            raise FileNotFoundError(f"Knowledge dataset file not found: {file_path}")

        df = pd.read_csv(file_path, delimiter=delimiter, encoding=encoding, **kwargs)

        if self.column_mapping:
            df = df.rename(columns=self.column_mapping)

        for col in self.normalize_columns:
            if col in df.columns:
                norm_col = f"{col}_normalized"
                df[norm_col] = (
                    df[col].astype(str).apply(self.normalizer.normalize)
                )

        records = df.to_dict(orient="records")
        return KnowledgeDataset(
            source_name=self.source_name,
            records=records,
            dataframe=df,
            metadata={"file_path": str(file_path), "row_count": len(df)},
        )


class JSONKnowledgeLoader(BaseKnowledgeLoader):
    """Generic JSON Knowledge dataset loader."""

    def __init__(
        self,
        source_name: str = "json_dataset",
        normalizer: Optional[DrugNormalizer] = None,
    ):
        super().__init__(normalizer=normalizer)
        self.source_name = source_name

    def load(
        self,
        source: Union[str, Path],
        encoding: str = "utf-8",
        **kwargs: Any,
    ) -> KnowledgeDataset:
        file_path = Path(source)
        if not file_path.exists():
            raise FileNotFoundError(f"Knowledge dataset file not found: {file_path}")

        with open(file_path, "r", encoding=encoding) as f:
            data = json.load(f)

        if isinstance(data, list):
            records = data
        elif isinstance(data, dict) and "results" in data:
            records = data["results"]
        elif isinstance(data, dict):
            records = [data]
        else:
            records = []

        df = pd.DataFrame(records) if records else pd.DataFrame()
        return KnowledgeDataset(
            source_name=self.source_name,
            records=records,
            dataframe=df,
            metadata={"file_path": str(file_path), "record_count": len(records)},
        )


class OpenFDAKnowledgeLoader(CSVKnowledgeLoader):
    """Pluggable Loader for openFDA validated datasets."""

    def __init__(self, normalizer: Optional[DrugNormalizer] = None):
        super().__init__(
            source_name="openfda",
            column_mapping={"brand_name": "drug_brand", "generic_name": "drug_generic"},
            normalize_columns=["drug_generic", "drug_brand"],
            normalizer=normalizer,
        )


class DDInterKnowledgeLoader(CSVKnowledgeLoader):
    """Pluggable Loader for DDInter drug interaction datasets."""

    def __init__(self, normalizer: Optional[DrugNormalizer] = None):
        super().__init__(
            source_name="ddinter",
            column_mapping={"drug_a": "drug_1", "drug_b": "drug_2"},
            normalize_columns=["drug_1", "drug_2"],
            normalizer=normalizer,
        )


class RxNormKnowledgeLoader(CSVKnowledgeLoader):
    """Pluggable Loader for RxNorm concept and mapping datasets."""

    def __init__(self, normalizer: Optional[DrugNormalizer] = None):
        super().__init__(
            source_name="rxnorm",
            column_mapping={"rxcui": "rxnorm_id", "str": "concept_name"},
            normalize_columns=["concept_name"],
            normalizer=normalizer,
        )


class DailyMedKnowledgeLoader(JSONKnowledgeLoader):
    """Pluggable Loader for DailyMed structured drug label datasets."""

    def __init__(self, normalizer: Optional[DrugNormalizer] = None):
        super().__init__(
            source_name="dailymed",
            normalizer=normalizer,
        )
