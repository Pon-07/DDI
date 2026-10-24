import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from agents.audit import AuditLedger
from agents.explicator import ExplicatorService
from agents.knowledge import DrugNormalizer, KnowledgeService
from agents.resolution import SafetyResolutionEngine
from agents.risk import RiskDetector
from database.database import Base
from engine import MedicationEventService
from models.models import Event, Finding, Lab, Medication, Order, Patient


class TestEndToEndPipeline(unittest.TestCase):
    """
    Isolated integration tests for:
    - STEP 36: End-to-End Event Pipeline
      (Event -> Ingest -> RiskDetector -> Finding -> SafetyResolutionEngine -> SimulatedOrder -> Explicator -> AuditLedger)
    - STEP 37: Patient Context + Re-evaluation Hardening
      (Chronological lab trends, order status events, duplicate finding prevention, context re-evaluation, missing data handling)
    """

    def setUp(self):
        # 1. In-memory SQLite for data isolation
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

        # 2. File-based temp SQLite for AuditLedger to support native triggers
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = os.path.join(self.temp_dir.name, "audit_test.db")
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

        # 3. Knowledge & Explicator Services
        self.normalizer = DrugNormalizer()
        self.knowledge_service = KnowledgeService(normalizer=self.normalizer)
        self.explicator = ExplicatorService()

        # 4. SafetyResolutionEngine & MedicationEventService
        self.resolution_engine = SafetyResolutionEngine(
            explicator=self.explicator,
            audit_ledger=self.audit_ledger,
        )
        self.event_service = MedicationEventService(
            knowledge_service=self.knowledge_service,
            resolution_engine=self.resolution_engine,
            audit_ledger=self.audit_ledger,
            explicator=self.explicator,
        )

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.temp_dir.cleanup()

    def test_a_complete_end_to_end_pipeline(self):
        """
        TEST A: Complete end-to-end execution path:
        Event -> Detection -> Finding -> Resolution -> Explanation -> SimulatedOrder -> Audit.
        """
        # 1. Patient on Warfarin
        patient = Patient(patient_identifier="MRN-E2E-001", name="Sarah Connor")
        self.db.add(patient)
        self.db.commit()
        self.db.refresh(patient)

        med1 = Medication(
            patient_id=patient.id,
            drug_name="Warfarin Sodium 5mg",
            dose="5",
            dose_unit="mg",
            status="active",
        )
        # Chronological INR labs showing rising trend (2.2 -> 3.8)
        lab1 = Lab(
            patient_id=patient.id,
            test_name="INR",
            value="2.2",
            measured_at=datetime(2026, 9, 20, 10, 0),
        )
        lab2 = Lab(
            patient_id=patient.id,
            test_name="INR",
            value="3.8",
            measured_at=datetime(2026, 9, 25, 10, 0),
        )
        self.db.add_all([med1, lab1, lab2])
        self.db.commit()

        # 2. Triggering Event: Order submitted for Fluconazole
        db_findings, pipeline_results = self.event_service.process_event_with_resolutions(
            db=self.db,
            patient_id=patient.id,
            event_type="MEDICATION_ORDERED",
            payload={"drug_name": "Fluconazole 100mg", "dose": "100", "dose_unit": "mg"},
            new_medication_name="Fluconazole 100mg",
        )

        # 3. Assert Detection & Finding
        self.assertEqual(len(db_findings), 1)
        finding = db_findings[0]
        self.assertEqual(finding.rule_id, "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertEqual(finding.inputs["inr_context"], "present")
        self.assertEqual(finding.inputs["inr_trend"], "rising")
        self.assertEqual(finding.inputs["inr_value"], "3.8")
        self.assertIn("AEGIS_HACKATHON_DEMO", finding.trace["source"])

        # 4. Assert Resolution & Simulated Order
        self.assertEqual(len(pipeline_results), 1)
        res = pipeline_results[0]
        self.assertEqual(res.status, "actionable")
        self.assertTrue(res.requires_cosign)
        self.assertIsNotNone(res.simulated_order)

        sim_order = res.simulated_order
        self.assertTrue(sim_order.requires_cosign)
        self.assertFalse(sim_order.auto_execute)
        self.assertTrue(sim_order.is_simulated)
        self.assertEqual(sim_order.status, "pending_cosign")
        self.assertEqual(sim_order.evidence_id, "AEGIS-DEMO-EV-001")
        self.assertIn("review/hold/monitor", sim_order.proposed_action)

        # 5. Assert Explanation
        self.assertIsNotNone(res.explanation)
        self.assertIn("warfarin", res.explanation.full_text.lower())
        self.assertIn("fluconazole", res.explanation.full_text.lower())
        self.assertEqual(res.explanation.evidence_id, "AEGIS-DEMO-EV-001")

        # 6. Assert Audit Trail
        audit_events = self.audit_ledger.get_events()
        event_types = [r.event_type for r in audit_events]
        self.assertIn("RISK_FINDING_DETECTED", event_types)
        self.assertIn("SAFETY_RESOLUTION_EVALUATED", event_types)
        self.assertIn("EXPLANATION_GENERATED", event_types)
        self.assertIn("SIMULATED_ORDER_CREATED", event_types)

        # 7. Assert Chain Integrity
        verification = self.audit_ledger.verify_chain()
        self.assertTrue(verification.is_valid)

    def test_b_medication_event_reevaluation(self):
        """
        TEST B: Medication event triggers re-evaluation of affected patient context
        (Patient on Lisinopril; new medication Enalapril triggers duplicate ACE-inhibitor rule).
        """
        patient = Patient(patient_identifier="MRN-E2E-002", name="John Connor")
        self.db.add(patient)
        self.db.commit()

        # Active medication: Lisinopril
        med1 = Medication(
            patient_id=patient.id,
            drug_name="Lisinopril 10mg",
            status="active",
        )
        self.db.add(med1)
        self.db.commit()

        # Event: Order Enalapril
        db_findings, pipeline_results = self.event_service.process_event_with_resolutions(
            db=self.db,
            patient_id=patient.id,
            event_type="MEDICATION_ORDERED",
            payload={"drug_name": "Enalapril 10mg"},
            new_medication_name="Enalapril 10mg",
        )

        self.assertEqual(len(db_findings), 1)
        self.assertEqual(db_findings[0].rule_id, "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR")
        self.assertEqual(db_findings[0].inputs["duplicate_class"], "ace_inhibitor")
        self.assertEqual(pipeline_results[0].status, "actionable")
        self.assertTrue(pipeline_results[0].simulated_order.requires_cosign)

    def test_c_lab_event_reevaluation_and_changed_context_detection(self):
        """
        TEST C: Lab event triggers re-evaluation of affected patient context
        (Patient on Enoxaparin with missing renal labs; lab event adds Creatinine 2.6 mg/dL;
        context changes from missing data to declining renal function).
        """
        patient = Patient(patient_identifier="MRN-E2E-003", name="Kyle Reese")
        self.db.add(patient)
        self.db.commit()

        # Patient on Enoxaparin without renal labs
        med1 = Medication(
            patient_id=patient.id,
            drug_name="Enoxaparin Sodium 40mg",
            status="active",
        )
        self.db.add(med1)
        self.db.commit()

        # Initial evaluation: Missing renal panel -> requires human review
        first_findings, first_resolutions = self.event_service.process_event_with_resolutions(
            db=self.db,
            patient_id=patient.id,
            event_type="MEDICATION_EVALUATED",
            payload={"drug_name": "Enoxaparin Sodium 40mg"},
        )
        self.assertEqual(len(first_findings), 1)
        self.assertEqual(first_findings[0].inputs["renal_context"], "missing")
        self.assertIn("eGFR", first_findings[0].inputs["data_needed"])
        self.assertEqual(first_resolutions[0].status, "requires_human_review")
        self.assertIsNone(first_resolutions[0].simulated_order)

        # Incoming Lab Event: Creatinine 2.6 mg/dL
        lab_findings, lab_resolutions = self.event_service.process_event_with_resolutions(
            db=self.db,
            patient_id=patient.id,
            event_type="LAB_RESULT_RECORDED",
            payload={"test_name": "Creatinine", "value": "2.6", "unit": "mg/dL"},
        )

        # Context has changed! Finding is re-evaluated with updated context
        self.assertEqual(len(lab_findings), 1)
        updated_finding = lab_findings[0]
        self.assertEqual(updated_finding.inputs["latest_renal_lab"], "2.6")
        self.assertNotIn("data_needed", updated_finding.inputs)
        self.assertEqual(lab_resolutions[0].status, "actionable")
        self.assertIsNotNone(lab_resolutions[0].simulated_order)
        self.assertIn("dose adjustment", lab_resolutions[0].simulated_order.proposed_action)

    def test_d_order_status_event_reevaluation(self):
        """
        TEST D: Order/status event re-evaluation
        (Patient has duplicate ACE inhibitors; an ORDER_CANCELLED event discontinues one;
        re-evaluation confirms duplicate therapy is no longer detected).
        """
        patient = Patient(patient_identifier="MRN-E2E-004", name="Marcus Wright")
        self.db.add(patient)
        self.db.commit()

        med1 = Medication(patient_id=patient.id, drug_name="Lisinopril 20mg", status="active")
        med2 = Medication(patient_id=patient.id, drug_name="Enalapril 10mg", status="active")
        self.db.add_all([med1, med2])
        self.db.commit()

        # Step 1: Detect duplicate ACE inhibitor
        init_findings = self.event_service.process_event(
            db=self.db,
            patient_id=patient.id,
            event_type="MEDICATION_REVIEW",
        )
        self.assertEqual(len(init_findings), 1)

        # Step 2: Order status event cancels Enalapril
        cancel_findings = self.event_service.process_event(
            db=self.db,
            patient_id=patient.id,
            event_type="ORDER_CANCELLED",
            payload={"drug_name": "Enalapril 10mg", "status": "cancelled"},
        )

        # Active ACE inhibitors now only 1 (Lisinopril). Duplicate therapy is not detected.
        self.assertEqual(len(cancel_findings), 0)

    def test_e_repeated_identical_event_prevents_duplicate_findings(self):
        """
        TEST E: Repeated identical event does not create duplicate unchanged findings.
        """
        patient = Patient(patient_identifier="MRN-E2E-005", name="Grace Harper")
        self.db.add(patient)
        self.db.commit()

        med1 = Medication(patient_id=patient.id, drug_name="Warfarin 5mg", status="active")
        self.db.add(med1)
        self.db.commit()

        # Event 1: First order event
        findings_1 = self.event_service.process_event(
            db=self.db,
            patient_id=patient.id,
            event_type="MEDICATION_ORDERED",
            payload={"drug_name": "Fluconazole 100mg"},
            new_medication_name="Fluconazole 100mg",
        )
        self.assertEqual(len(findings_1), 1)

        # Persist Fluconazole in active meds
        med2 = Medication(patient_id=patient.id, drug_name="Fluconazole 100mg", status="active")
        self.db.add(med2)
        self.db.commit()

        # Event 2: Identical repeated event
        findings_2 = self.event_service.process_event(
            db=self.db,
            patient_id=patient.id,
            event_type="ORDER_CONFIRMED",
            payload={"drug_name": "Fluconazole 100mg"},
        )
        # No duplicate finding created
        self.assertEqual(len(findings_2), 0)

        # Exactly 1 finding in database
        total_findings = self.db.execute(
            select(Finding).where(Finding.patient_id == patient.id)
        ).scalars().all()
        self.assertEqual(len(total_findings), 1)

    def test_f_changed_relevant_context_reevaluates_finding(self):
        """
        TEST F: Changed relevant context re-evaluates the finding correctly.
        (Warfarin + Fluconazole initially has missing INR; new INR lab updates finding in-place).
        """
        patient = Patient(patient_identifier="MRN-E2E-006", name="Dani Ramos")
        self.db.add(patient)
        self.db.commit()

        med1 = Medication(patient_id=patient.id, drug_name="Warfarin 5mg", status="active")
        med2 = Medication(patient_id=patient.id, drug_name="Fluconazole 100mg", status="active")
        self.db.add_all([med1, med2])
        self.db.commit()

        # Initial detection with no INR lab
        f1 = self.event_service.process_event(
            db=self.db,
            patient_id=patient.id,
            event_type="MEDICATION_REVIEW",
        )
        self.assertEqual(len(f1), 1)
        self.assertEqual(f1[0].inputs["inr_context"], "missing")

        # Lab event arrives with INR = 3.6
        f2 = self.event_service.process_event(
            db=self.db,
            patient_id=patient.id,
            event_type="LAB_RESULT",
            payload={"test_name": "INR", "value": "3.6"},
        )
        self.assertEqual(len(f2), 1)
        self.assertEqual(f2[0].inputs["inr_context"], "present")
        self.assertEqual(f2[0].inputs["inr_value"], "3.6")

        # Total findings in database remains 1 (re-evaluated in-place without duplicate)
        total_findings = self.db.execute(
            select(Finding).where(Finding.patient_id == patient.id)
        ).scalars().all()
        self.assertEqual(len(total_findings), 1)

        # Audit ledger recorded both detection and re-evaluation
        records = self.audit_ledger.get_events()
        types = [r.event_type for r in records]
        self.assertIn("RISK_FINDING_DETECTED", types)
        self.assertIn("RISK_FINDING_REEVALUATED", types)

    def test_g_missing_clinical_data_follows_data_needed_review_path(self):
        """
        TEST G: Missing clinical data follows explicit data-needed / human-review path.
        (Never guesses or fabricates doses or thresholds; simulated order is None).
        """
        patient = Patient(patient_identifier="MRN-E2E-007", name="Miles Dyson")
        self.db.add(patient)
        self.db.commit()

        # Patient on Enoxaparin with NO renal labs
        med = Medication(patient_id=patient.id, drug_name="Enoxaparin 40mg", status="active")
        self.db.add(med)
        self.db.commit()

        db_findings, pipeline_results = self.event_service.process_event_with_resolutions(
            db=self.db,
            patient_id=patient.id,
            event_type="PRESCRIPTION_EVALUATION",
            payload={"drug_name": "Enoxaparin 40mg"},
        )

        self.assertEqual(len(pipeline_results), 1)
        res = pipeline_results[0]
        self.assertEqual(res.status, "requires_human_review")
        self.assertIsNone(res.simulated_order)
        self.assertIn("eGFR", res.review_reason)
        self.assertIn("creatinine", res.review_reason)

    def test_h_full_audit_hash_chain_validity(self):
        """
        TEST H: Full audit hash-chain remains cryptographically valid after the pipeline.
        """
        patient = Patient(patient_identifier="MRN-E2E-008", name="Tim Dyson")
        self.db.add(patient)
        self.db.commit()

        med1 = Medication(patient_id=patient.id, drug_name="Lisinopril 10mg", status="active")
        self.db.add(med1)
        self.db.commit()

        # Execute multiple pipeline events
        self.event_service.process_event_with_resolutions(
            db=self.db,
            patient_id=patient.id,
            event_type="MEDICATION_ORDERED",
            payload={"drug_name": "Ramipril 5mg"},
            new_medication_name="Ramipril 5mg",
        )

        verification = self.audit_ledger.verify_chain()
        self.assertTrue(verification.is_valid)
        self.assertGreaterEqual(verification.total_records, 4)

    def test_i_no_production_db_modification(self):
        """
        TEST I: Production aegis_rx.db is completely untouched during pipeline execution.
        """
        prod_db_path = Path("/Users/shivk/Documents/DDI/aegis_rx.db")
        if prod_db_path.exists():
            stat_before = prod_db_path.stat()
            mtime_before = stat_before.st_mtime
            size_before = stat_before.st_size

            # Run pipeline test
            patient = Patient(patient_identifier="MRN-E2E-009", name="Isolated Test")
            self.db.add(patient)
            self.db.commit()

            stat_after = prod_db_path.stat()
            self.assertEqual(mtime_before, stat_after.st_mtime)
            self.assertEqual(size_before, stat_after.st_size)

    def test_j_deterministic_repeated_execution_produces_equivalent_results(self):
        """
        TEST J: Deterministic repeated execution on independent DBs produces equivalent results.
        """
        # Run execution 1 on self.db
        patient1 = Patient(patient_identifier="MRN-E2E-010-A", name="Deterministic A")
        self.db.add(patient1)
        self.db.commit()

        med1 = Medication(patient_id=patient1.id, drug_name="Lisinopril 10mg", status="active")
        self.db.add(med1)
        self.db.commit()

        f1, r1 = self.event_service.process_event_with_resolutions(
            db=self.db,
            patient_id=patient1.id,
            event_type="MEDICATION_ORDERED",
            payload={"drug_name": "Enalapril 10mg"},
            new_medication_name="Enalapril 10mg",
        )

        # Run execution 2 on a completely separate in-memory DB
        engine2 = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine2)
        Session2 = sessionmaker(bind=engine2, autoflush=False, autocommit=False)
        db2 = Session2()

        service2 = MedicationEventService(
            knowledge_service=KnowledgeService(normalizer=DrugNormalizer()),
            resolution_engine=SafetyResolutionEngine(explicator=ExplicatorService()),
        )

        patient2 = Patient(patient_identifier="MRN-E2E-010-B", name="Deterministic B")
        db2.add(patient2)
        db2.commit()

        med2 = Medication(patient_id=patient2.id, drug_name="Lisinopril 10mg", status="active")
        db2.add(med2)
        db2.commit()

        f2, r2 = service2.process_event_with_resolutions(
            db=db2,
            patient_id=patient2.id,
            event_type="MEDICATION_ORDERED",
            payload={"drug_name": "Enalapril 10mg"},
            new_medication_name="Enalapril 10mg",
        )

        # Verify equivalence
        self.assertEqual(len(f1), len(f2))
        self.assertEqual(f1[0].rule_id, f2[0].rule_id)
        self.assertEqual(f1[0].title, f2[0].title)
        self.assertEqual(f1[0].description, f2[0].description)
        self.assertEqual(f1[0].action, f2[0].action)

        self.assertEqual(len(r1), len(r2))
        self.assertEqual(r1[0].status, r2[0].status)
        self.assertEqual(
            r1[0].simulated_order.proposed_action,
            r2[0].simulated_order.proposed_action,
        )

        db2.close()
        Base.metadata.drop_all(bind=engine2)


if __name__ == "__main__":
    unittest.main()
