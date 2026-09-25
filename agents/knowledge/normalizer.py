import re
from typing import Dict, Optional, Set


class DrugNormalizer:
    """
    Standard drug name normalizer for standardizing active ingredients and medication names.
    Supports salt stripping, dosage form removal, strength stripping, and alias resolution.
    """

    DEFAULT_SALTS: Set[str] = {
        "acetate",
        "besylate",
        "bromide",
        "calcium",
        "citrate",
        "dihydrate",
        "dipropionate",
        "fumarate",
        "gluconate",
        "hcl",
        "hydrochloride",
        "iodide",
        "lactate",
        "maleate",
        "mesylate",
        "monohydrate",
        "nitrate",
        "phosphate",
        "potassium",
        "sodium",
        "succinate",
        "sulfate",
        "sulphate",
        "tartrate",
        "tosylate",
        "trihydrate",
        "valerate",
    }

    DEFAULT_DOSAGE_FORMS: Set[str] = {
        "cap",
        "caps",
        "capsule",
        "capsules",
        "cream",
        "delayed release",
        "dr",
        "drops",
        "elixir",
        "er",
        "extended release",
        "gel",
        "im",
        "inhaler",
        "inj",
        "injection",
        "iv",
        "lotion",
        "ointment",
        "ophthalmic",
        "oral",
        "otic",
        "patch",
        "solution",
        "spray",
        "sq",
        "sr",
        "subcutaneous",
        "suppository",
        "suspension",
        "syrup",
        "tab",
        "tablets",
        "tablet",
        "topical",
        "xl",
        "xr",
    }

    def __init__(
        self,
        alias_map: Optional[Dict[str, str]] = None,
        strip_salts: bool = True,
        strip_dosage_forms: bool = True,
        custom_salts: Optional[Set[str]] = None,
        custom_dosage_forms: Optional[Set[str]] = None,
    ):
        self.alias_map: Dict[str, str] = {}
        if alias_map:
            self.register_aliases(alias_map)
        self.strip_salts = strip_salts
        self.strip_dosage_forms = strip_dosage_forms
        self.salts: Set[str] = (
            set(custom_salts) if custom_salts is not None else set(self.DEFAULT_SALTS)
        )
        self.dosage_forms: Set[str] = (
            set(custom_dosage_forms)
            if custom_dosage_forms is not None
            else set(self.DEFAULT_DOSAGE_FORMS)
        )

    def register_alias(self, alias: str, canonical_name: str) -> None:
        """Register a brand name or synonym alias to a canonical drug name."""
        cleaned_alias = self._clean_string(alias)
        cleaned_canonical = self._clean_string(canonical_name)
        if cleaned_alias and cleaned_canonical:
            self.alias_map[cleaned_alias] = cleaned_canonical

    def register_aliases(self, alias_map: Dict[str, str]) -> None:
        """Register multiple aliases from a dictionary."""
        for alias, canonical in alias_map.items():
            self.register_alias(alias, canonical)

    def _clean_string(self, text: str) -> str:
        """Clean string by lowercasing, replacing non-word characters, and stripping whitespace."""
        if not text:
            return ""
        text = text.lower().strip()
        text = re.sub(r"[^\w\s-]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def normalize(self, drug_name: str) -> str:
        """
        Normalize a drug name to its canonical active ingredient name.

        Pipeline:
        1. Lowercase and trim.
        2. Direct alias mapping check.
        3. Strip dosage strengths (including decimal units: 0.125mg, 2.5 ml, 500 mg).
        4. Strip standalone numeric dose numbers.
        5. Strip non-alphanumeric punctuation.
        6. Strip common salt forms and dosage route/form tokens.
        7. Re-check alias mapping on canonical tokens.
        """
        if not drug_name or not isinstance(drug_name, str):
            return ""

        text = drug_name.lower().strip()
        if not text:
            return ""

        cleaned_direct = self._clean_string(text)
        if cleaned_direct in self.alias_map:
            return self.alias_map[cleaned_direct]

        # Strip dosage strengths before removing decimal points (e.g. '0.125mg', '500 mg', '2.5 ml')
        text = re.sub(
            r"\b\d+(\.\d+)?\s*(mg|mcg|g|ug|ml|l|iu|meq|%)\b", " ", text
        )
        # Strip standalone numbers if any (e.g. '0.125', '20')
        text = re.sub(r"\b\d+(\.\d+)?\b", " ", text)

        # Replace non-alphanumeric separators
        text = re.sub(r"[^\w\s-]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        tokens = text.split()
        filtered_tokens = []

        for token in tokens:
            if self.strip_dosage_forms and token in self.dosage_forms:
                continue
            if self.strip_salts and token in self.salts:
                continue
            filtered_tokens.append(token)

        result = " ".join(filtered_tokens).strip()

        if result in self.alias_map:
            return self.alias_map[result]

        return result if result else text

