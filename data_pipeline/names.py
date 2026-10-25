"""Drug-name helpers (qualifier parsing + normalisation via the repo's DrugNormalizer)."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, Tuple

from agents.knowledge.normalizer import DrugNormalizer

_QUALIFIER_RE = re.compile(r"^(?P<base>.*?)\s*\((?P<qual>[^()]*)\)\s*$")

# Qualifiers that denote a route / dosage form (a different product with its own interaction
# profile in DDInter, e.g. "Atropine (ophthalmic)"), as opposed to a salt or formulation.
ROUTE_FORM_TERMS = frozenset(
    {
        "ophthalmic", "topical", "nasal", "otic", "oral", "inhalation", "inhaled",
        "intravenous", "intramuscular", "subcutaneous", "injection", "vaginal",
        "rectal", "transdermal", "intrathecal", "intranasal", "sublingual",
        "buccal", "dermal", "intravitreal", "systemic",
    }
)

_normalizer = DrugNormalizer()


def clean_display_name(name: str) -> str:
    """NFKC-normalise and collapse whitespace; case and punctuation are preserved."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", name or "")).strip()


def split_qualifier(name: str) -> Tuple[str, Optional[str]]:
    """'Atropine (ophthalmic)' -> ('Atropine', 'ophthalmic'). No qualifier -> (name, None)."""
    name = clean_display_name(name)
    m = _QUALIFIER_RE.match(name)
    if m and m.group("base").strip():
        return m.group("base").strip(), m.group("qual").strip().lower() or None
    return name, None


def qualifier_kind(qualifier: Optional[str]) -> Optional[str]:
    if not qualifier:
        return None
    if qualifier in ROUTE_FORM_TERMS:
        return "route_form"
    if qualifier in _normalizer.salts:
        return "salt"
    return "other"


def normalize_name(name: str) -> str:
    """Repo-standard normalisation (see agents.knowledge.normalizer)."""
    return _normalizer.normalize(clean_display_name(name))
