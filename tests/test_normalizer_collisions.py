"""
Regression tests for DrugNormalizer collisions found in the DDInter data audit.

Before the fix these distinct drugs were merged into one normalised name:
  calcium/potassium/sodium chloride -> "chloride"; sodium/potassium bicarbonate -> "bicarbonate";
  zinc and magnesium salts -> "zinc"/"magnesium"; ferrous fumarate/gluconate -> "ferrous";
  iodide i-123 / i-131 -> "i-".
"""

import pytest

from agents.knowledge.normalizer import DrugNormalizer

n = DrugNormalizer()


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Calcium chloride", "calcium chloride"),
        ("Potassium chloride", "potassium chloride"),
        ("Sodium chloride", "sodium chloride"),
        ("Sodium bicarbonate", "sodium bicarbonate"),
        ("Potassium bicarbonate", "potassium bicarbonate"),
        ("Calcium carbonate", "calcium carbonate"),
        ("Zinc sulfate", "zinc sulfate"),
        ("Zinc acetate", "zinc acetate"),
        ("Magnesium sulfate", "magnesium sulfate"),
        ("Ferrous fumarate", "ferrous fumarate"),
        ("Ferrous gluconate", "ferrous gluconate"),
    ],
)
def test_distinct_compounds_stay_distinct(name, expected):
    assert n.normalize(name) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        # trailing counter-ions are still stripped (unchanged behaviour)
        ("Metformin Hydrochloride", "metformin"),
        ("Phenytoin sodium", "phenytoin"),
        ("Atorvastatin calcium", "atorvastatin"),
        ("Amlodipine Besylate", "amlodipine"),
        ("Benzylpenicillin (potassium)", "benzylpenicillin"),
        ("Metformin 500mg Oral Tablet", "metformin"),
        ("Digoxin 0.125 mg Tablet", "digoxin"),
    ],
)
def test_existing_salt_and_dose_stripping_unchanged(name, expected):
    assert n.normalize(name) == expected


def test_radioisotopes_keep_their_mass_number():
    a, b = n.normalize("Iodide i-123"), n.normalize("Iodide i-131")
    assert a != b
    assert "123" in a and "131" in b


def test_all_chloride_salts_distinct():
    names = ["Calcium chloride", "Potassium chloride", "Sodium chloride"]
    assert len({n.normalize(x) for x in names}) == 3


def test_empty_inputs_unchanged():
    assert n.normalize("") == ""
    assert n.normalize("   ") == ""
    assert n.normalize(None) == ""
