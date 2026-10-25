"""
DDInter lists each unordered pair once per category file, so the same interaction can arrive as
(A,B) and (B,A). The adapter must store it once and still never synthesise reverse rows.
Drug names below are SYNTHETIC.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.knowledge.ddinter_adapter import DDInterIngestionAdapter
from database.database import Base
from models import InteractionEvidence


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


def rec(a, b, ev, sev="Major"):
    return {"drug_a": a, "drug_b": b, "severity": sev, "interaction_description": sev, "evidence": ev, "source": "ddinter"}


def test_reversed_pair_is_not_stored_twice(session):
    ad = DDInterIngestionAdapter(session, source_version="t")
    res = ad.ingest_records([rec("Fakeone", "Faketwo", "DDInter1 | DDInter2"),
                             rec("Faketwo", "Fakeone", "DDInter2 | DDInter1")])
    assert session.query(InteractionEvidence).count() == 1
    assert res.interaction_evidences_created == 1 and res.interaction_evidences_skipped == 1


def test_reversed_pair_in_a_later_run_is_still_recognised(session):
    DDInterIngestionAdapter(session, source_version="t").ingest_records([rec("Fakeone", "Faketwo", "DDInter1 | DDInter2")])
    again = DDInterIngestionAdapter(session, source_version="t")  # fresh adapter -> cache rebuilt from DB
    again.ingest_records([rec("Faketwo", "Fakeone", "DDInter2 | DDInter1")])
    assert session.query(InteractionEvidence).count() == 1


def test_no_reverse_row_is_synthesised(session):
    DDInterIngestionAdapter(session, source_version="t").ingest_records([rec("Fakeone", "Faketwo", "DDInter1 | DDInter2")])
    row = session.query(InteractionEvidence).one()
    assert (row.drug_a, row.drug_b) == ("Fakeone", "Faketwo")


def test_distinct_pairs_with_same_normalised_names_but_different_ids_are_kept(session):
    # 'Fakeone' vs 'Fakeone (topical)' normalise to different strings or share one; evidence ids differ
    ad = DDInterIngestionAdapter(session, source_version="t")
    ad.ingest_records([rec("Fakeone", "Faketwo", "DDInter1 | DDInter3"),
                       rec("Fakeone (topical)", "Faketwo", "DDInter2 | DDInter3")])
    assert session.query(InteractionEvidence).count() == 2
