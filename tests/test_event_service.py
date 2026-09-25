import unittest
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from agents.knowledge import (
    DrugNormalizer,
    InteractionEvidence,
    KnowledgeService,
)
from database.database import Base
from engine import MedicationEventService
from models import Event, Finding, Medication, Patient


class TestMedicationEventService(unittest.TestCase):
    """Unit tests for the MedicationEventService event pipeline."""

    def setUp(self):
        # Create an in-memory SQLite database for isolated test execution
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

        # Set up KnowledgeService with synthetic interaction
        self.normalizer = DrugNormalizer()
        self.knowledge_service = KnowledgeService(normalizer=self.normalizer)
        self.knowledge_service.add_interactions(
            [
                InteractionEvidence(
                    drug_a="lisinopril",
                    drug_b="spironolactone",
                    description="Combined ACE inhibitor and potassium-sparing diuretic therapy risks hyperkalemia.",
                    source="DDInter",
                    source_version="v2.0",
                    evidence_id="DDI-EV-100",
                )
            ]
        )
        self.event_service = MedicationEventService(knowledge_service=self.knowledge_service)

        # Create a test patient
        self.patient = Patient(
            patient_identifier="MRN-TEST-EVT",
            name="Sarah Connor",
        )
        self.db.add(self.patient)
        self.db.commit()
        self.db.refresh(self.patient)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_no_interaction_event(self):
        # Add initial medication
        med1 = Medication(
            patient_id=self.patient.id,
            drug_name="Metformin 500mg",
            status="active",
        )
        self.db.add(med1)
        self.db.commit()

        # Process new order event with benign drug
        new_findings = self.event_service.process_event(
            db=self.db,
            patient_id=self.patient.id,
            event_type="MEDICATION_ORDERED",
            payload={"drug_name": "Atorvastatin 20mg", "dose": "20mg"},
            new_medication_name="Atorvastatin 20mg",
        )

        # Assert no findings created
        self.assertEqual(len(new_findings), 0)

        # Assert event was stored in SQLite
        events = self.db.execute(select(Event).where(Event.patient_id == self.patient.id)).scalars().all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "MEDICATION_ORDERED")

    def test_known_interaction_evidence_event(self):
        # Patient is already on Lisinopril
        med1 = Medication(
            patient_id=self.patient.id,
            drug_name="Lisinopril 10mg",
            status="active",
        )
        self.db.add(med1)
        self.db.commit()

        # Order Spironolactone (known interaction)
        new_findings = self.event_service.process_event(
            db=self.db,
            patient_id=self.patient.id,
            event_type="ORDER_SUBMITTED",
            payload={"drug_name": "Spironolactone 25mg", "order_id": 99},
            new_medication_name="Spironolactone 25mg",
        )

        # Assert finding was created and stored in DB
        self.assertEqual(len(new_findings), 1)
        finding = new_findings[0]
        self.assertEqual(finding.rule_id, "DDI-EV-100")
        self.assertEqual(finding.patient_id, self.patient.id)
        self.assertIn("hyperkalemia", finding.description)
        self.assertEqual(finding.severity, "undetermined")
        self.assertIsNone(finding.action)

        # Verify findings table in SQLite
        db_findings = self.db.execute(
            select(Finding).where(Finding.patient_id == self.patient.id)
        ).scalars().all()
        self.assertEqual(len(db_findings), 1)
        self.assertEqual(db_findings[0].trace["evidence_id"], "DDI-EV-100")
        self.assertEqual(db_findings[0].trace["source"], "DDInter")

    def test_missing_knowledge_event(self):
        # Patient on an unfamiliar medication with no interaction knowledge
        med1 = Medication(
            patient_id=self.patient.id,
            drug_name="ExperimentalDrug-A",
            status="active",
        )
        self.db.add(med1)
        self.db.commit()

        new_findings = self.event_service.process_event(
            db=self.db,
            patient_id=self.patient.id,
            event_type="MEDICATION_ORDERED",
            payload={"drug_name": "ExperimentalDrug-B"},
            new_medication_name="ExperimentalDrug-B",
        )

        # Must not guess or generate findings when knowledge is missing
        self.assertEqual(len(new_findings), 0)

        # Event is still recorded
        events = self.db.execute(select(Event).where(Event.patient_id == self.patient.id)).scalars().all()
        self.assertEqual(len(events), 1)

    def test_duplicate_event_prevents_duplicate_findings(self):
        # Patient on Lisinopril
        med1 = Medication(
            patient_id=self.patient.id,
            drug_name="Lisinopril 10mg",
            status="active",
        )
        self.db.add(med1)
        self.db.commit()

        # First event triggers finding
        first_findings = self.event_service.process_event(
            db=self.db,
            patient_id=self.patient.id,
            event_type="ORDER_SUBMITTED",
            payload={"drug_name": "Spironolactone 25mg", "order_id": 101},
            new_medication_name="Spironolactone 25mg",
        )
        self.assertEqual(len(first_findings), 1)

        # Add Spironolactone to patient's active meds
        med2 = Medication(
            patient_id=self.patient.id,
            drug_name="Spironolactone 25mg",
            status="active",
        )
        self.db.add(med2)
        self.db.commit()

        # Second duplicate event
        second_findings = self.event_service.process_event(
            db=self.db,
            patient_id=self.patient.id,
            event_type="ORDER_CONFIRMED",
            payload={"drug_name": "Spironolactone 25mg", "order_id": 101},
        )
        # Duplicate finding is NOT recreated
        self.assertEqual(len(second_findings), 0)

        # Total findings in database remains exactly 1
        all_findings = self.db.execute(
            select(Finding).where(Finding.patient_id == self.patient.id)
        ).scalars().all()
        self.assertEqual(len(all_findings), 1)

        # Both events are recorded in `events` table
        all_events = self.db.execute(
            select(Event).where(Event.patient_id == self.patient.id)
        ).scalars().all()
        self.assertEqual(len(all_events), 2)


if __name__ == "__main__":
    unittest.main()
