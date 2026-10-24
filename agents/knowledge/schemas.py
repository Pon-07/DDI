from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DrugKnowledge(BaseModel):
    """
    Normalized drug entity representation preserving provenance.
    Data representation only - does not contain clinical logic.
    """

    model_config = ConfigDict(extra="forbid")

    drug_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Original drug name as stated in source data",
    )
    normalized_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Normalized canonical drug name",
    )
    rxnorm_code: Optional[str] = Field(
        None,
        max_length=50,
        description="RxNorm Concept Unique Identifier (RxCUI) if available",
    )
    source: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Source dataset provider (e.g., openfda, ddinter, rxnorm, dailymed)",
    )
    source_version: Optional[str] = Field(
        None,
        max_length=50,
        description="Dataset version or release date",
    )
    label_id: Optional[str] = Field(
        None,
        max_length=100,
        description="Associated label ID or external identifier",
    )


class InteractionEvidence(BaseModel):
    """
    Raw interaction evidence statement between two drugs preserving provenance.
    Data representation only - does not contain severity decisions or recommendations.
    """

    model_config = ConfigDict(extra="forbid")

    drug_a: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="First drug in the interaction pair",
    )
    drug_b: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Second drug in the interaction pair",
    )
    description: str = Field(
        ...,
        min_length=1,
        description="Reported interaction description or mechanism text",
    )
    severity: Optional[str] = Field(
        None,
        max_length=50,
        description="Reported severity in source dataset (e.g. Major, Moderate, Minor, Unknown)",
    )
    source: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Source dataset provider (e.g., ddinter, openfda, dailymed)",
    )
    source_version: Optional[str] = Field(
        None,
        max_length=50,
        description="Dataset version or release date",
    )
    evidence_id: Optional[str] = Field(
        None,
        max_length=100,
        description="Unique identifier of the evidence item in the source dataset",
    )


class LabelEvidence(BaseModel):
    """
    Product labeling section text excerpt preserving provenance.
    Data representation only - does not contain clinical rule execution.
    """

    model_config = ConfigDict(extra="forbid")

    drug_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Drug name associated with the package insert / label",
    )
    section: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Section name (e.g., boxed_warning, drug_interactions, contraindications)",
    )
    text: str = Field(
        ...,
        min_length=1,
        description="Full text excerpt from the label section",
    )
    source: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Source dataset provider (e.g., dailymed, openfda)",
    )
    source_version: Optional[str] = Field(
        None,
        max_length=50,
        description="Label version, SPL set ID, or release date",
    )
    evidence_id: Optional[str] = Field(
        None,
        max_length=100,
        description="External identifier for label evidence",
    )
