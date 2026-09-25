from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class AuditRecord(BaseModel):
    """
    Schema representing an immutable, tamper-evident audit ledger record.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="Monotonically increasing sequence ID of the audit entry")
    timestamp: str = Field(..., description="ISO-8601 formatted UTC timestamp of the recorded event")
    actor: str = Field(..., min_length=1, max_length=100, description="Identifier or role of the executing entity")
    event_type: str = Field(..., min_length=1, max_length=100, description="Category or name of the recorded event")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Structured event payload (sanitized of sensitive PHI)")
    prev_hash: str = Field(..., min_length=1, max_length=64, description="SHA-256 hash of the preceding ledger entry")
    hash: str = Field(..., min_length=64, max_length=64, description="SHA-256 hash of the current entry computed with prev_hash")


class ChainVerificationResult(BaseModel):
    """
    Verification outcome containing chain integrity diagnostics and error details.
    """

    model_config = ConfigDict(extra="forbid")

    is_valid: bool = Field(..., description="True if the entire ledger chain integrity is cryptographically valid")
    total_records: int = Field(..., ge=0, description="Total number of evaluated records in the ledger")
    errors: List[str] = Field(default_factory=list, description="Diagnostic error descriptions if tampering is detected")
    tampered_record_id: Optional[int] = Field(None, description="ID of the first record where integrity failed")
