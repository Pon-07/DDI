from typing import Optional
from fastapi import Depends

from agents.audit.ledger import AuditLedger
from agents.explicator.service import ExplicatorService
from agents.knowledge import DrugNormalizer, KnowledgeService
from agents.resolution.resolver import SafetyResolutionEngine
from engine.event_service import MedicationEventService

_normalizer = DrugNormalizer()
_knowledge_service = KnowledgeService(normalizer=_normalizer)
_default_audit_ledger = AuditLedger(db_path="aegis_rx.db")
_default_explicator = ExplicatorService()
_default_resolution_engine = SafetyResolutionEngine(
    explicator=_default_explicator,
    audit_ledger=_default_audit_ledger,
)
_default_event_service = MedicationEventService(
    knowledge_service=_knowledge_service,
    resolution_engine=_default_resolution_engine,
    rule_context=_default_resolution_engine,
    explicator=_default_explicator,
    audit_ledger=_default_audit_ledger,
)


def get_audit_ledger() -> AuditLedger:
    return _default_audit_ledger


def get_knowledge_service() -> KnowledgeService:
    return _knowledge_service


def get_explicator_service() -> ExplicatorService:
    return _default_explicator


def get_resolution_engine(
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    explicator: ExplicatorService = Depends(get_explicator_service),
) -> SafetyResolutionEngine:
    if audit_ledger is _default_audit_ledger and explicator is _default_explicator:
        return _default_resolution_engine
    return SafetyResolutionEngine(
        explicator=explicator,
        audit_ledger=audit_ledger,
    )


def get_event_service(
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
    resolution_engine: SafetyResolutionEngine = Depends(get_resolution_engine),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    explicator: ExplicatorService = Depends(get_explicator_service),
) -> MedicationEventService:
    if (
        knowledge_service is _knowledge_service
        and resolution_engine is _default_resolution_engine
        and audit_ledger is _default_audit_ledger
        and explicator is _default_explicator
    ):
        return _default_event_service
    return MedicationEventService(
        knowledge_service=knowledge_service,
        resolution_engine=resolution_engine,
        rule_context=resolution_engine,
        explicator=explicator,
        audit_ledger=audit_ledger,
    )
