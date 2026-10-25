"""DailyMed SPL parser tests. The XML below is a tiny SYNTHETIC document (invented drug) used only
to exercise the parser; it is NOT DailyMed data."""

import io
import zipfile

import pytest

from data_pipeline.dailymed import iter_dailymed_zip, parse_spl_xml

SPL = b"""<?xml version="1.0" encoding="UTF-8"?>
<document xmlns="urn:hl7-org:v3">
  <setId root="00000000-0000-0000-0000-000000000001"/>
  <title>FAKEDRUGOL tablets (synthetic test label)</title>
  <effectiveTime value="20260803"/>
  <component><structuredBody>
    <component><section><code code="34068-7"/><title>DOSAGE</title><text><paragraph>Take one tablet daily.</paragraph></text></section></component>
    <component><section><code code="34073-7"/><text>Avoid   fakedrugtwo.</text></section></component>
    <component><section><code code="99999-9"/><text>ignored section</text></section></component>
    <component><section><code code="34070-3"/><text>   </text></section></component>
  </structuredBody></component>
  <subject><manufacturedProduct><manufacturedProduct><name>FAKEDRUGOL</name>
    <ingredient classCode="ACTIB"><ingredientSubstance><name>FAKEDRUGOL HYDROCHLORIDE</name></ingredientSubstance></ingredient>
  </manufacturedProduct></manufacturedProduct></subject>
</document>"""


def test_parse_extracts_identity_and_loinc_sections_only():
    rec = parse_spl_xml(SPL, category="prescription")
    assert rec["set_id"] == "00000000-0000-0000-0000-000000000001"
    assert rec["product_name"] == "FAKEDRUGOL"
    assert rec["active_ingredients"] == ["FAKEDRUGOL HYDROCHLORIDE"]
    assert rec["effective_time"] == "20260803"
    got = {s["section"]: s["text"] for s in rec["sections"]}
    assert got == {"dosage_and_administration": "Take one tablet daily.", "drug_interactions": "Avoid fakedrugtwo."}


@pytest.mark.parametrize("payload", [
    b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa">]><document xmlns="urn:hl7-org:v3"/>',
    b'<document xmlns="urn:hl7-org:v3"><!ENTITY x "y"></document>',
])
def test_doctype_and_entities_are_rejected(payload):
    with pytest.raises(ValueError):
        parse_spl_xml(payload)


def test_nested_zip_with_category_filter(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("label.xml", SPL)
    outer_path = tmp_path / "monthly.zip"
    with zipfile.ZipFile(outer_path, "w") as z:
        z.writestr("prescription/a.zip", inner.getvalue())
        z.writestr("animal/b.zip", inner.getvalue())
    recs = list(iter_dailymed_zip(outer_path, categories={"prescription"}))
    assert len(recs) == 1 and recs[0]["category"] == "prescription"
    assert len(list(iter_dailymed_zip(outer_path))) == 2
