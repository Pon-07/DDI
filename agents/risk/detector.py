from typing import Any, Dict, List, Optional, Set, Tuple

from agents.knowledge.schemas import InteractionEvidence
from agents.knowledge.service import KnowledgeService
from agents.risk.schemas import Finding, RiskAssessmentReport


class RiskDetector:
    """
    Deterministic risk detection engine evaluating patient medication regimens
    against validated drug interaction and safety knowledge.
    Does not use LLMs, heuristics, or unvalidated threshold assumptions.
    """

    def __init__(
        self,
        knowledge_service: KnowledgeService,
        patient_context: Optional[Any] = None,
        medications: Optional[List[Any]] = None,
        labs: Optional[List[Any]] = None,
    ):
        self.knowledge_service = knowledge_service
        self.patient_context = patient_context
        self.medications = medications or []
        self.labs = labs or []

    def _extract_drug_name(self, med: Any) -> Optional[str]:
        """Safely extract drug name from ORM model, Pydantic schema, dict, or string."""
        if isinstance(med, str):
            name = med.strip()
            return name if name else None

        if isinstance(med, dict):
            status = med.get("status", "active")
            if status and str(status).lower() in {"inactive", "discontinued", "cancelled"}:
                return None
            name = med.get("drug_name") or med.get("name")
            return str(name).strip() if name else None

        # ORM model or Pydantic schema object
        status = getattr(med, "status", "active")
        if status and str(status).lower() in {"inactive", "discontinued", "cancelled"}:
            return None

        name = getattr(med, "drug_name", None) or getattr(med, "name", None)
        return str(name).strip() if name else None

    def _extract_patient_id(self, patient_context: Any) -> Optional[Any]:
        """Extract patient identifier or ID from patient context."""
        if patient_context is None:
            return None
        if isinstance(patient_context, (int, str)):
            return patient_context
        if isinstance(patient_context, dict):
            return patient_context.get("id") or patient_context.get("patient_identifier")
        return getattr(patient_context, "id", None) or getattr(patient_context, "patient_identifier", None)

    def detect(
        self,
        patient_context: Optional[Any] = None,
        medications: Optional[List[Any]] = None,
        labs: Optional[List[Any]] = None,
    ) -> List[Finding]:
        """
        Execute deterministic risk detection for active medication pairs.

        Returns:
            List[Finding]: List of identified safety findings with preserved provenance.
        """
        ctx = patient_context if patient_context is not None else self.patient_context
        med_list = medications if medications is not None else self.medications
        lab_list = labs if labs is not None else self.labs

        patient_id = self._extract_patient_id(ctx)

        # Extract active drug names
        active_drugs: List[str] = []
        for item in med_list:
            d_name = self._extract_drug_name(item)
            if d_name and d_name not in active_drugs:
                active_drugs.append(d_name)

        if len(active_drugs) < 2:
            return []

        normalizer = self.knowledge_service.normalizer
        findings: List[Finding] = []
        seen_finding_keys: Set[Tuple[str, str, str]] = set()

        # Evaluate all unique drug pairs
        for i in range(len(active_drugs)):
            for j in range(i + 1, len(active_drugs)):
                drug_a = active_drugs[i]
                drug_b = active_drugs[j]

                norm_a = normalizer.normalize(drug_a).lower().strip()
                norm_b = normalizer.normalize(drug_b).lower().strip()

                if not norm_a or not norm_b:
                    continue

                # Query interaction evidence for the pair
                interactions = self.knowledge_service.get_interaction_pair(drug_a, drug_b)

                for evidence in interactions:
                    ev_a = normalizer.normalize(evidence.drug_a).lower().strip()
                    ev_b = normalizer.normalize(evidence.drug_b).lower().strip()

                    # Dedup key
                    evidence_key = evidence.evidence_id or f"{ev_a}_{ev_b}_{evidence.description[:30]}"
                    finding_dedup = (norm_a, norm_b, evidence_key)

                    if finding_dedup in seen_finding_keys:
                        continue
                    seen_finding_keys.add(finding_dedup)

                    rule_id = (
                        str(evidence.evidence_id)
                        if evidence.evidence_id
                        else f"DDI_{norm_a}_{norm_b}"
                    )

                    finding = Finding(
                        rule_id=rule_id,
                        severity="undetermined",
                        title=f"Documented Interaction: {drug_a} + {drug_b}",
                        description=evidence.description,
                        action=None,
                        inputs={
                            "drug_a": drug_a,
                            "drug_b": drug_b,
                            "patient_id": patient_id,
                            "lab_count": len(lab_list),
                        },
                        trace={
                            "rule_id": rule_id,
                            "evidence_id": evidence.evidence_id,
                            "source": evidence.source,
                            "source_version": evidence.source_version,
                            "matched_pair": [norm_a, norm_b],
                        },
                        source=evidence.source,
                    )
                    findings.append(finding)

        return findings

    def evaluate(
        self,
        patient_context: Optional[Any] = None,
        medications: Optional[List[Any]] = None,
        labs: Optional[List[Any]] = None,
    ) -> RiskAssessmentReport:
        """
        Run detection and package results into a RiskAssessmentReport.
        """
        ctx = patient_context if patient_context is not None else self.patient_context
        med_list = medications if medications is not None else self.medications
        lab_list = labs if labs is not None else self.labs

        findings = self.detect(patient_context=ctx, medications=med_list, labs=lab_list)
        patient_id = self._extract_patient_id(ctx)

        return RiskAssessmentReport(
            patient_id=patient_id,
            findings=findings,
            evaluated_medications_count=len(med_list),
            total_findings_count=len(findings),
        )
