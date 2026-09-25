from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

from agents.knowledge.ddinter_loader import DDInterLoader
from agents.knowledge.normalizer import DrugNormalizer
from agents.knowledge.openfda_loader import OpenFDALoader
from agents.knowledge.schemas import DrugKnowledge, InteractionEvidence, LabelEvidence


class KnowledgeService:
    """
    In-memory knowledge service for loading, indexing, and querying medication knowledge.
    Provides deterministic drug entity lookup, interaction evidence retrieval, and label section queries.
    """

    def __init__(self, normalizer: Optional[DrugNormalizer] = None):
        self.normalizer = normalizer or DrugNormalizer()
        self._drugs: List[DrugKnowledge] = []
        self._interactions: List[InteractionEvidence] = []
        self._label_evidences: List[LabelEvidence] = []

        # Index caches for fast retrieval
        self._drugs_by_normalized: Dict[str, List[DrugKnowledge]] = {}
        self._drugs_by_raw_lower: Dict[str, List[DrugKnowledge]] = {}
        self._interactions_by_drug: Dict[str, List[InteractionEvidence]] = {}
        self._interactions_by_canonical_pair: Dict[Tuple[str, str], List[InteractionEvidence]] = {}
        self._seen_interaction_fingerprints: Set[Tuple[str, str, str, str, str, str, str]] = set()
        self._label_evidence_by_drug: Dict[str, List[LabelEvidence]] = {}

    def add_drug(self, drug: DrugKnowledge) -> None:
        """Add a single DrugKnowledge entity and update internal indexes."""
        self._drugs.append(drug)
        norm_key = drug.normalized_name.lower().strip()
        raw_key = drug.drug_name.lower().strip()

        self._drugs_by_normalized.setdefault(norm_key, []).append(drug)
        self._drugs_by_raw_lower.setdefault(raw_key, []).append(drug)

    def add_drugs(self, drugs: List[DrugKnowledge]) -> None:
        """Add multiple DrugKnowledge entities."""
        for drug in drugs:
            self.add_drug(drug)

    def add_interaction(self, interaction: InteractionEvidence) -> bool:
        """
        Add a single InteractionEvidence item and index under canonical symmetric drug pair and individual drug endpoints.
        Deterministically deduplicates identical records.
        Preserves conflicting evidence (differing severity, descriptions, or sources) without silent merging.
        Returns True if added, False if duplicate.
        """
        norm_a = self.normalizer.normalize(interaction.drug_a).lower().strip()
        norm_b = self.normalizer.normalize(interaction.drug_b).lower().strip()
        if not norm_a or not norm_b:
            return False

        pair = tuple(sorted([norm_a, norm_b]))
        fingerprint = (
            pair[0],
            pair[1],
            str(interaction.source or "").lower().strip(),
            str(interaction.source_version or "").lower().strip(),
            str(interaction.evidence_id or "").lower().strip(),
            str(interaction.severity or "").lower().strip(),
            str(interaction.description or "").strip(),
        )

        if fingerprint in self._seen_interaction_fingerprints:
            return False

        self._seen_interaction_fingerprints.add(fingerprint)
        self._interactions.append(interaction)
        self._interactions_by_canonical_pair.setdefault(pair, []).append(interaction)

        raw_a = interaction.drug_a.lower().strip()
        raw_b = interaction.drug_b.lower().strip()
        for key in {norm_a, norm_b, raw_a, raw_b}:
            if key:
                self._interactions_by_drug.setdefault(key, []).append(interaction)
        return True

    def add_interactions(self, interactions: List[InteractionEvidence]) -> None:
        """Add multiple InteractionEvidence items."""
        for interaction in interactions:
            self.add_interaction(interaction)

    def add_label_evidence(self, evidence: LabelEvidence) -> None:
        """Add a single LabelEvidence item and update internal indexes."""
        self._label_evidences.append(evidence)
        norm_drug = self.normalizer.normalize(evidence.drug_name).lower().strip()
        raw_drug = evidence.drug_name.lower().strip()

        for key in {norm_drug, raw_drug}:
            if key:
                self._label_evidence_by_drug.setdefault(key, []).append(evidence)

    def add_label_evidences(self, evidences: List[LabelEvidence]) -> None:
        """Add multiple LabelEvidence items."""
        for evidence in evidences:
            self.add_label_evidence(evidence)

    def load_openfda_csv(
        self,
        file_path: Union[str, Path],
        source_version: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Load an OpenFDA validated CSV dataset into the knowledge service."""
        loader = OpenFDALoader(
            normalizer=self.normalizer,
            source_version=source_version,
        )
        drugs, evidences = loader.load_csv(file_path, **kwargs)
        self.add_drugs(drugs)
        self.add_label_evidences(evidences)

    def load_ddinter_csv(
        self,
        file_path: Union[str, Path],
        source_version: Optional[str] = None,
        **kwargs,
    ) -> List[Dict[str, object]]:
        """Load a DDInter validated CSV dataset into the knowledge service."""
        loader = DDInterLoader(
            normalizer=self.normalizer,
            source_version=source_version,
        )
        evidences, errors = loader.load_csv(file_path, **kwargs)
        self.add_interactions(evidences)
        return errors

    def get_drug(self, drug_name: str) -> Optional[DrugKnowledge]:
        """
        Deterministic, case-insensitive drug entity lookup by brand or generic name.
        Uses DrugNormalizer to resolve salts, dosage forms, and brand synonyms.
        """
        if not drug_name or not isinstance(drug_name, str):
            return None

        raw_key = drug_name.lower().strip()
        norm_key = self.normalizer.normalize(drug_name).lower().strip()

        if norm_key in self._drugs_by_normalized and self._drugs_by_normalized[norm_key]:
            return self._drugs_by_normalized[norm_key][0]

        if raw_key in self._drugs_by_raw_lower and self._drugs_by_raw_lower[raw_key]:
            return self._drugs_by_raw_lower[raw_key][0]

        return None

    def search_drug(self, drug_name: str) -> List[DrugKnowledge]:
        """
        Search for drugs matching a substring query against drug name or normalized name.
        """
        if not drug_name or not isinstance(drug_name, str):
            return []

        query = drug_name.lower().strip()
        if not query:
            return []

        norm_query = self.normalizer.normalize(drug_name).lower().strip()
        matched: List[DrugKnowledge] = []
        seen_ids: Set[int] = set()

        for drug in self._drugs:
            if id(drug) in seen_ids:
                continue

            d_name_lower = drug.drug_name.lower()
            d_norm_lower = drug.normalized_name.lower()

            if (
                query in d_name_lower
                or (norm_query and norm_query in d_norm_lower)
                or query in d_norm_lower
            ):
                matched.append(drug)
                seen_ids.add(id(drug))

        return matched

    def get_interactions(self, drug_name: str) -> List[InteractionEvidence]:
        """
        Retrieve all raw drug-drug interaction evidence where the queried drug is either drug_a or drug_b.
        """
        if not drug_name or not isinstance(drug_name, str):
            return []

        norm_key = self.normalizer.normalize(drug_name).lower().strip()
        raw_key = drug_name.lower().strip()

        results: List[InteractionEvidence] = []
        seen_ids: Set[int] = set()

        for key in [norm_key, raw_key]:
            if key in self._interactions_by_drug:
                for item in self._interactions_by_drug[key]:
                    if id(item) not in seen_ids:
                        results.append(item)
                        seen_ids.add(id(item))

        return results

    @staticmethod
    def is_demo_rule(evidence: InteractionEvidence) -> bool:
        """Determine if an interaction evidence item originates from hackathon/demo rules."""
        src = str(evidence.source or "").upper().strip()
        ev_id = str(evidence.evidence_id or "").upper().strip()
        return src == "AEGIS_HACKATHON_DEMO" or ev_id.startswith("AEGIS-DEMO")

    def get_interaction_pair(
        self,
        drug_a: str,
        drug_b: str,
        include_demo: bool = True,
    ) -> List[InteractionEvidence]:
        """
        Retrieve raw interaction evidence for a specific pair of medications (symmetric: A+B == B+A).
        Deterministic, sorted output preserving full provenance.
        Handles unknown drugs safely (returns []).
        Optionally excludes demo/hackathon rules to separate validated evidence.
        """
        if not drug_a or not drug_b or not isinstance(drug_a, str) or not isinstance(drug_b, str):
            return []

        norm_a = self.normalizer.normalize(drug_a).lower().strip()
        norm_b = self.normalizer.normalize(drug_b).lower().strip()

        if not norm_a or not norm_b:
            return []

        canonical_pair = tuple(sorted([norm_a, norm_b]))
        candidates = self._interactions_by_canonical_pair.get(canonical_pair, [])

        if not include_demo:
            candidates = [c for c in candidates if not self.is_demo_rule(c)]

        # Return deterministically sorted by source, source_version, evidence_id, severity, description
        return sorted(
            candidates,
            key=lambda x: (
                str(x.source or "").lower(),
                str(x.source_version or "").lower(),
                str(x.evidence_id or "").lower(),
                str(x.severity or "").lower(),
                str(x.description or ""),
            ),
        )

    def get_validated_interactions(
        self, drug_a: str, drug_b: str
    ) -> List[InteractionEvidence]:
        """Retrieve only validated evidence for a drug pair (excluding demo rules)."""
        return self.get_interaction_pair(drug_a, drug_b, include_demo=False)

    def get_demo_interactions(
        self, drug_a: str, drug_b: str
    ) -> List[InteractionEvidence]:
        """Retrieve only demo rules for a drug pair."""
        all_items = self.get_interaction_pair(drug_a, drug_b, include_demo=True)
        return [item for item in all_items if self.is_demo_rule(item)]

    def has_conflicting_evidence(self, drug_a: str, drug_b: str) -> bool:
        """
        Check if multiple distinct evidence records exist for this drug pair with differing severity ratings.
        Preserves all evidence without silently merging or averaging.
        """
        evidences = self.get_interaction_pair(drug_a, drug_b)
        if len(evidences) < 2:
            return False
        severities = {
            str(e.severity).lower().strip()
            for e in evidences
            if e.severity and str(e.severity).strip()
        }
        return len(severities) > 1

    def get_label_evidence(
        self,
        drug_name: str,
        section: Optional[str] = None,
    ) -> List[LabelEvidence]:
        """
        Retrieve all product label excerpts for a given drug, optionally filtered by section.
        """
        if not drug_name or not isinstance(drug_name, str):
            return []

        norm_key = self.normalizer.normalize(drug_name).lower().strip()
        raw_key = drug_name.lower().strip()

        candidate_evidences: List[LabelEvidence] = []
        seen_ids: Set[int] = set()

        for key in [norm_key, raw_key]:
            if key in self._label_evidence_by_drug:
                for item in self._label_evidence_by_drug[key]:
                    if id(item) not in seen_ids:
                        candidate_evidences.append(item)
                        seen_ids.add(id(item))

        if section is None:
            return candidate_evidences

        target_section = section.lower().strip()
        return [
            e for e in candidate_evidences if e.section.lower() == target_section
        ]
