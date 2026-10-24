"""
DailyMed SPL ingestion (monthly update ZIP -> nested ZIPs -> SPL XML).

STATUS: the DailyMed archive (dm_spl_monthly_update_aug2026.zip) is NOT in this repository
(only validation notes are). This module is ready for it and is unit-tested on a tiny
synthetic SPL fixture, but ``build`` reports DailyMed as ``missing`` until the ZIP is
placed at ``data_validation/dailymed/*.zip``.

Safety: XML with a DOCTYPE/ENTITY declaration is rejected (SPL never needs one; this blocks
entity-expansion attacks), and per-entry size is capped so a ZIP bomb cannot exhaust memory.
Sections are matched by LOINC code, never by guessing from prose.
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

MAX_XML_BYTES = 80 * 1024 * 1024
MAX_NESTED_ZIP_BYTES = 200 * 1024 * 1024

# SPL section LOINC codes -> staging section names (same names as the openFDA sections).
LOINC_SECTIONS = {
    "34068-7": "dosage_and_administration",
    "34070-3": "contraindications",
    "34073-7": "drug_interactions",
    "43685-7": "warnings_and_cautions",
    "43684-0": "use_in_specific_populations",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el: ET.Element, name: str) -> List[ET.Element]:
    return [c for c in el if _local(c.tag) == name]


def _find_all(el: ET.Element, name: str) -> Iterator[ET.Element]:
    for node in el.iter():
        if _local(node.tag) == name:
            yield node


def _text_of(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", " ".join(el.itertext())).strip()


def parse_spl_xml(data: bytes, category: Optional[str] = None) -> Dict[str, Any]:
    """Parse one SPL document into a label record + section records."""
    if len(data) > MAX_XML_BYTES:
        raise ValueError("SPL XML exceeds size limit")
    head = data[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data[:65536].upper():
        raise ValueError("DOCTYPE/ENTITY declarations are not allowed in SPL input")
    root = ET.fromstring(data)

    set_id = None
    for el in root:
        if _local(el.tag) == "setId":
            set_id = el.get("root")
    title = _text_of(next(iter(_children(root, "title")), None)) or None
    eff = next(iter(_children(root, "effectiveTime")), None)
    effective = eff.get("value") if eff is not None else None

    product_name = None
    for mp in _find_all(root, "manufacturedProduct"):
        for nm in _children(mp, "name"):
            product_name = _text_of(nm) or product_name
        if product_name:
            break
    if not product_name:
        for mm in _find_all(root, "manufacturedMedicine"):
            for nm in _children(mm, "name"):
                product_name = _text_of(nm) or product_name

    ingredients: List[str] = []
    for ing in _find_all(root, "ingredient"):
        if ing.get("classCode") == "ACTIB":
            for sub in _find_all(ing, "ingredientSubstance"):
                for nm in _children(sub, "name"):
                    t = _text_of(nm)
                    if t and t not in ingredients:
                        ingredients.append(t)

    sections: List[Dict[str, Any]] = []
    for sec in _find_all(root, "section"):
        code_el = next(iter(_children(sec, "code")), None)
        code = code_el.get("code") if code_el is not None else None
        name = LOINC_SECTIONS.get(code or "")
        if not name:
            continue
        txt = _text_of(next(iter(_children(sec, "text")), None))
        if txt:
            sections.append({"section": name, "loinc": code, "text": txt, "char_len": len(txt)})

    return {
        "set_id": set_id,
        "title": title,
        "effective_time": effective,
        "product_name": product_name,
        "active_ingredients": ingredients,
        "category": category,
        "sections": sections,
    }


def _category_from_path(path: str) -> Optional[str]:
    parts = path.replace("\\", "/").lower().split("/")
    for cat in ("prescription", "otc", "animal", "homeopathic", "other"):
        if cat in parts[:-1]:
            return cat
    return None


def iter_dailymed_zip(zip_path: Path, categories: Optional[set] = None) -> Iterator[Dict[str, Any]]:
    """Yield parsed label records from a DailyMed monthly ZIP (nested ZIPs supported)."""
    with zipfile.ZipFile(zip_path) as outer:
        for info in outer.infolist():
            if info.is_dir():
                continue
            cat = _category_from_path(info.filename)
            if categories and cat not in categories:
                continue
            lower = info.filename.lower()
            if lower.endswith(".zip"):
                if info.file_size > MAX_NESTED_ZIP_BYTES:
                    continue
                with zipfile.ZipFile(io.BytesIO(outer.read(info))) as inner:
                    for sub in inner.infolist():
                        if sub.filename.lower().endswith(".xml") and sub.file_size <= MAX_XML_BYTES:
                            yield parse_spl_xml(inner.read(sub), category=cat)
            elif lower.endswith(".xml") and info.file_size <= MAX_XML_BYTES:
                yield parse_spl_xml(outer.read(info), category=cat)
