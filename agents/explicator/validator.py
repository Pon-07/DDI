import re
from typing import Any, Dict, List, Optional, Set, Tuple

KNOWN_DRUG_NAMES: Set[str] = {
    "warfarin", "aspirin", "lisinopril", "spironolactone", "fluconazole",
    "metformin", "enoxaparin", "atorvastatin", "cimetidine", "enalapril",
    "clopidogrel", "heparin", "digoxin", "amiodarone", "simvastatin",
    "ibuprofen", "naproxen", "furosemide", "omeprazole", "apixaban",
    "rivaroxaban", "dabigatran", "losartan", "amlodipine", "carvedilol",
    "metoprolol", "diltiazem", "verapamil", "prednisone", "ciprofloxacin",
    "levofloxacin", "azithromycin", "clarithromycin", "erythromycin",
    "glipizide", "tramadol", "oxycodone", "morphine", "acetaminophen",
}


class ExplanationValidator:
    """
    Validator ensuring that all referenced medication names and numeric values
    in an explanation exist in the supplied Finding inputs and evidence trace.
    Prevents hallucination or fabrication of unverified clinical values.
    """

    def __init__(self, known_drugs: Optional[Set[str]] = None):
        self.known_drugs = known_drugs if known_drugs is not None else KNOWN_DRUG_NAMES

    @staticmethod
    def _extract_numbers(text: str) -> Set[str]:
        """Extract all numeric tokens from text."""
        if not text:
            return set()
        return set(re.findall(r"\b\d+(?:\.\d+)?\b", str(text)))

    @staticmethod
    def _gather_allowed_numbers(finding_data: Dict[str, Any]) -> Set[str]:
        """Gather all valid numeric tokens from the finding source payload and trace."""
        allowed: Set[str] = set()

        # Add numbers from finding fields
        for field in ["rule_id", "title", "description", "action", "source", "evidence_id"]:
            val = finding_data.get(field)
            if val:
                allowed.update(ExplanationValidator._extract_numbers(str(val)))

        # Add numbers from inputs
        inputs = finding_data.get("inputs", {})
        if isinstance(inputs, dict):
            for k, v in inputs.items():
                if v is not None:
                    allowed.update(ExplanationValidator._extract_numbers(str(v)))

        # Add numbers from trace
        trace = finding_data.get("trace", {})
        if isinstance(trace, dict):
            for k, v in trace.items():
                if v is not None:
                    allowed.update(ExplanationValidator._extract_numbers(str(v)))

        return allowed

    @staticmethod
    def _gather_allowed_drugs(finding_data: Dict[str, Any]) -> Set[str]:
        """Gather all active and trace drug names present in the finding."""
        allowed: Set[str] = set()

        inputs = finding_data.get("inputs", {})
        if isinstance(inputs, dict):
            for key in ["drug_a", "drug_b", "medication", "drug_name", "name"]:
                if key in inputs and inputs[key]:
                    allowed.add(str(inputs[key]).lower().strip())

        trace = finding_data.get("trace", {})
        if isinstance(trace, dict):
            matched_pair = trace.get("matched_pair")
            if matched_pair and isinstance(matched_pair, list):
                for d in matched_pair:
                    allowed.add(str(d).lower().strip())

        return allowed

    def validate_explanation(
        self,
        explanation_text: str,
        finding_data: Dict[str, Any],
    ) -> Tuple[bool, List[str]]:
        """
        Validate that explanation_text does not contain unverified numeric values
        or foreign drug references outside the finding context.
        """
        errors: List[str] = []

        if not explanation_text or not explanation_text.strip():
            return False, ["Explanation text is empty."]

        # 1. Validate numeric tokens: every number must exist in the trace
        allowed_numbers = self._gather_allowed_numbers(finding_data)
        explanation_numbers = self._extract_numbers(explanation_text)

        for num in explanation_numbers:
            if num not in allowed_numbers:
                errors.append(
                    f"Unverified numeric value '{num}' found in explanation text that is not in the source finding or trace."
                )

        # 2. Validate medication names: every drug name must exist in the trace
        allowed_drugs = self._gather_allowed_drugs(finding_data)
        for drug in self.known_drugs:
            if re.search(rf"\b{re.escape(drug)}\b", explanation_text, re.IGNORECASE):
                is_allowed = False
                for ad in allowed_drugs:
                    ad_clean = ad.lower().strip()
                    if drug in ad_clean or ad_clean in drug:
                        is_allowed = True
                        break
                if not is_allowed:
                    for field in ["title", "description", "action", "rule_id", "evidence_id"]:
                        f_val = str(finding_data.get(field) or "").lower()
                        if drug in f_val:
                            is_allowed = True
                            break
                if not is_allowed:
                    errors.append(
                        f"Unverified drug name '{drug}' found in explanation text that is not in the source finding or trace."
                    )

        return (len(errors) == 0, errors)
