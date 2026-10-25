import os
import tempfile
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.audit.ledger import AuditLedger
from app.main import app
from database.database import Base, get_db
from engine.auth.models import AuthBase, User
from engine.auth.providers.local import LocalDemoOTPProvider
from engine.auth.service import AuthService
from models import Event, Lab, Medication, Order, Patient
from tests.test_client import LocalTestClient


class TestRoleDashboards(unittest.TestCase):
    """
    Integration tests for Role-Specific Dashboards:
    - Doctor Dashboard (cosign queue, patient profiles, prescribing sandbox)
    - Nurse Dashboard (MAR records, vitals/lab entry)
    - Pharmacist Dashboard (DDI matrix, CYP enzymes, renal adjustments)
    - Admin Dashboard (system health, audit chain, gateway switcher)
    - Web Console HTML endpoint (/app)
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        AuthBase.metadata.create_all(bind=self.engine)

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        # Seed clinical patients
        p1 = Patient(patient_identifier="DEMO-PT-001", name="Sarah Jenkins", sex="F")
        p2 = Patient(patient_identifier="DEMO-PT-002", name="Robert Chen", sex="M")
        p3 = Patient(patient_identifier="DEMO-PT-003", name="Elena Rostova", sex="F")
        p4 = Patient(patient_identifier="DEMO-PT-004", name="Arthur Pendelton", sex="M")
        self.db.add_all([p1, p2, p3, p4])
        self.db.flush()

        m1 = Medication(patient_id=p1.id, drug_name="warfarin", dose="5", dose_unit="mg", frequency="daily")
        l1 = Lab(patient_id=p1.id, test_name="INR", value="3.4")
        m2 = Medication(patient_id=p2.id, drug_name="enoxaparin", dose="80", dose_unit="mg", frequency="daily")
        l2 = Lab(patient_id=p2.id, test_name="Creatinine", value="2.4")
        m3 = Medication(patient_id=p3.id, drug_name="lisinopril", dose="10", dose_unit="mg", frequency="daily")
        l3 = Lab(patient_id=p3.id, test_name="Potassium", value="5.3")
        self.db.add_all([m1, l1, m2, l2, m3, l3])
        self.db.flush()
        self.enoxaparin_order = Order(
            patient_id=p2.id,
            medication_id=m2.id,
            drug_name="Enoxaparin Sodium (Lovenox)",
            dose="80 mg / 0.8 mL",
            status="pending",
        )
        self.db.add(self.enoxaparin_order)
        self.db.commit()

        # Temp AuditLedger
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = os.path.join(self.temp_dir.name, "test_dash_audit.db")
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

        # Seed simulated order in audit ledger for cosign test
        self.audit_ledger.append_event(
            actor="SYSTEM_RESOLUTION_ENGINE",
            event_type="SIMULATED_ORDER_CREATED",
            payload={
                "simulation_id": "SIM-TEST-001",
                "patient_id": p1.id,
                "drug_name": "warfarin",
                "proposed_action": "Reduce warfarin dose by 50%",
                "action_type": "DOSE_REDUCTION",
                "rationale": "CYP2C9 inhibition from fluconazole",
                "requires_cosign": True,
            },
        )

        self.auth_service = AuthService(
            audit_ledger=self.audit_ledger,
            sms_provider=LocalDemoOTPProvider(demo_mode=True),
            demo_mode=True,
        )
        self.auth_service.seed_demo_users(self.db)
        from engine.auth.dependencies import get_current_user
        from engine.auth.models import User
        self.doctor_user = self.db.query(User).filter(User.role == "Doctor").first()
        self.pharmacist_user = self.db.query(User).filter(User.role == "Clinical Pharmacist").first()
        self.current_user = self.doctor_user

        app.dependency_overrides[get_db] = lambda: self.db
        from app.dependencies import get_audit_ledger
        from engine.auth.dependencies import get_auth_service
        app.dependency_overrides[get_audit_ledger] = lambda: self.audit_ledger
        app.dependency_overrides[get_auth_service] = lambda: self.auth_service
        app.dependency_overrides[get_current_user] = lambda: self.current_user

        self.client = LocalTestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        AuthBase.metadata.drop_all(bind=self.engine)
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_doctor_dashboard_data(self):
        """Verify Doctor dashboard returns patient rosters, pending cosigns, and metrics."""
        res = self.client.get("/api/dashboard/doctor")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["role"], "Doctor")
        self.assertIn("stats", data)
        self.assertGreaterEqual(data["stats"]["total_patients"], 2)
        self.assertEqual(data["stats"]["pending_cosigns_count"], 1)

        # Check pending cosign order
        self.assertEqual(len(data["pending_cosigns"]), 1)
        self.assertEqual(data["pending_cosigns"][0]["simulation_id"], "SIM-TEST-001")
        self.assertEqual(data["pending_cosigns"][0]["drug_name"], "warfarin")

    def test_nurse_dashboard_data(self):
        """Verify Nurse dashboard returns populated MAR records, vitals/labs, and shift statistics."""
        res = self.client.get("/api/dashboard/nurse")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["role"], "Nurse")
        self.assertEqual(data["title"], "Medication Administration Record & Inpatient Monitoring")
        
        # Verify Shift Statistics
        self.assertIn("stats", data)
        stats = data["stats"]
        self.assertIn("medications_due", stats)
        self.assertIn("medications_administered", stats)
        self.assertIn("medications_held", stats)
        self.assertIn("vitals_recorded", stats)
        self.assertIn("attention_required", stats)
        self.assertEqual(stats["medications_due"], 0)
        self.assertEqual(stats["medications_administered"], 0)

        # Verify MAR Schedule is populated with case-linked records
        self.assertIn("mar_schedule", data)
        self.assertEqual(len(data["mar_schedule"]), 3)
        self.assertFalse(data["mar_schedule_available"])
        
        # Check specific MAR record properties
        first_mar = data["mar_schedule"][0]
        self.assertEqual(first_mar["patient_name"], "Sarah Jenkins")
        self.assertIn("medication", first_mar)
        self.assertIn("dose", first_mar)
        self.assertIn("scheduled_time", first_mar)
        self.assertIn("status", first_mar)
        self.assertIsNone(first_mar["scheduled_time"])

        # Verify Inpatient Vitals / Labs section is populated
        self.assertIn("vitals_labs", data)
        self.assertEqual(len(data["vitals_labs"]), 3)
        first_vital = data["vitals_labs"][0]
        self.assertEqual(first_vital["patient_name"], "Sarah Jenkins")
        self.assertIn("parameter", first_vital)
        self.assertIn("value", first_vital)
        self.assertIn("unit", first_vital)
        self.assertIn("status", first_vital)

    def test_nurse_mar_action_workflow(self):
        """Verify Nurse marking medication administered or held records cryptographic audit event."""
        # 1. Nurse records medication administration
        nurse_user = self.db.query(User).filter_by(role="Nurse").first()
        self.current_user = nurse_user
        medication = self.db.query(Medication).filter(Medication.patient_id == self.db.query(Patient).filter_by(patient_identifier="DEMO-PT-001").first().id).first()
        admin_payload = {
            "mar_id": f"MED-{medication.id}",
            "action": "ADMINISTERED",
            "nurse_name": "Elena Rostova, RN",
            "notes": "Administered 2.5 mg PO per morning schedule."
        }
        res_admin = self.client.post("/api/dashboard/nurse/mar-action", json=admin_payload)
        self.assertEqual(res_admin.status_code, 200)
        admin_data = res_admin.json()
        self.assertEqual(admin_data["status"], "ADMINISTERED")
        self.assertEqual(self.db.query(Event).filter_by(event_type="MEDICATION_ADMINISTERED").count(), 1)
        self.assertIn("audit_hash", admin_data)
        self.assertEqual(len(admin_data["audit_hash"]), 64)

        # 2. Nurse records medication hold due to clinical alert
        hold_payload = {
            "mar_id": "MED-" + str(self.db.query(Medication).filter(Medication.patient_id == self.db.query(Patient).filter_by(patient_identifier="DEMO-PT-002").first().id).first().id),
            "action": "HELD",
            "nurse_name": "Elena Rostova, RN",
            "hold_reason": "INR elevated at 3.4; held pending physician order review.",
            "notes": "Safety hold executed."
        }
        res_hold = self.client.post("/api/dashboard/nurse/mar-action", json=hold_payload)
        self.assertEqual(res_hold.status_code, 200)
        hold_data = res_hold.json()
        self.assertEqual(hold_data["status"], "HELD")
        self.assertIn("audit_hash", hold_data)
        self.assertEqual(len(hold_data["audit_hash"]), 64)

    def test_nurse_record_vital_workflow(self):
        """Verify Nurse recording bedside vitals/POC labs evaluates thresholds and logs audit event."""
        self.current_user = self.db.query(User).filter_by(role="Nurse").first()
        vital_payload = {
            "case_id": "DEMO-PT-001",
            "patient_name": "Sarah Jenkins",
            "parameter": "Blood Pressure",
            "value": "132/84",
            "unit": "mmHg",
            "nurse_name": "Elena Rostova, RN"
        }
        res = self.client.post("/api/dashboard/nurse/record-vital", json=vital_payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "RECORDED")
        self.assertIn("audit_hash", data)
        self.assertEqual(len(data["audit_hash"]), 64)
        self.assertEqual(data["record"]["status"], "RECORDED")
        self.assertEqual(self.db.query(Lab).filter_by(test_name="Blood Pressure").count(), 1)

    def test_pharmacist_dashboard_data(self):
        """Verify Pharmacist dashboard returns Medication Fulfillment & Substitution metrics, availability queue, and local inventory."""
        self.current_user = self.pharmacist_user
        res = self.client.get("/api/dashboard/pharmacist")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["role"], "Clinical Pharmacist")
        self.assertEqual(data["title"], "Medication Fulfillment & Substitution")
        self.assertEqual(data["primary_purpose"], "Medication Availability & Brand Substitution Verification")

        # Verify 6 Summary Cards
        stats = data["stats"]
        self.assertIn("prescriptions_received", stats)
        self.assertIn("available", stats)
        self.assertIn("unavailable", stats)
        self.assertIn("substitution_requests", stats)
        self.assertIn("doctor_approval_pending", stats)
        self.assertIn("ready_for_dispensing", stats)

        # Verify Availability Queue
        self.assertIn("availability_queue", data)
        self.assertEqual(len(data["availability_queue"]), 1)
        self.assertFalse(data["inventory_available"])
        first_item = data["availability_queue"][0]
        self.assertIn("patient_id", first_item)
        self.assertIn("patient_name", first_item)
        self.assertIn("prescribed_medicine", first_item)
        self.assertIn("strength", first_item)
        self.assertIn("formulation", first_item)
        self.assertIn("availability", first_item)
        self.assertIn("substitution_status", first_item)

        # Verify Local Inventory and Secondary Reference
        self.assertIn("local_inventory", data)
        self.assertEqual(data["local_inventory"], [])
        self.assertIn("pharmacology_reference", data)

    def test_pharmacist_dispense_workflow(self):
        """Prevent a dispense-ready state when inventory is not persisted."""
        self.current_user = self.pharmacist_user
        payload = {
            "case_id": "DEMO-PT-002",
            "patient_id": self.db.query(Patient).filter_by(patient_identifier="DEMO-PT-002").first().id,
            "patient_identifier": "DEMO-PT-002",
            "patient_name": "Robert Chen",
            "medication": "Enoxaparin Sodium (Lovenox)",
            "strength": "80 mg / 0.8 mL",
            "formulation": "Tablet",
            "quantity": 30,
            "pharmacist_notes": "Stock verified in Bin A-04 (420 tabs available). Ready for dispensing."
        }
        res = self.client.post("/api/dashboard/pharmacist/dispense", json=payload)
        self.assertEqual(res.status_code, 409)
        self.assertIn("Inventory is not tracked", res.json()["detail"])

    def test_pharmacist_brand_substitution_request_and_doctor_approval_workflow(self):
        """Verify end-to-end brand substitution workflow: pharmacist suggest to doctor -> doctor approves -> ready for dispensing."""
        self.current_user = self.pharmacist_user
        patient = self.db.query(Patient).filter(Patient.patient_identifier == "DEMO-PT-002").first()
        medication = self.db.query(Medication).filter(Medication.patient_id == patient.id).first()
        # 1. Pharmacist sends substitution request for unavailable Enoxaparin (Lovenox) -> Clexane
        sub_payload = {
            "patient_id": patient.id,
            "order_id": self.enoxaparin_order.id,
            "medication_id": medication.id,
            "idempotency_key": "role-dashboard-request-key-1",
            "suggested_brand": "Clexane (Sanofi)",
            "reason": "Prescribed brand is currently unavailable in hospital pharmacy inventory.",
            "pharmacist_notes": "Bioequivalent low molecular weight heparin in stock (Bin D-12, 60 units)."
        }
        res_sub = self.client.post("/api/dashboard/pharmacist/substitution-request", json=sub_payload)
        self.assertEqual(res_sub.status_code, 200)
        sub_data = res_sub.json()
        self.assertEqual(sub_data["status"], "pending")
        self.assertIn("audit_hash", sub_data)
        request_id = sub_data["request_id"]

        # 2. Doctor verifies and approves the substitution
        self.current_user = self.doctor_user
        doc_payload = {
            "request_id": request_id,
            "decision": "APPROVED",
            "doctor_notes": "Approved. Bioequivalent formulation; continue anti-Xa monitoring protocol."
        }
        res_dec = self.client.post("/api/dashboard/doctor/substitution-decision", json=doc_payload)
        self.assertEqual(res_dec.status_code, 200)
        dec_data = res_dec.json()
        self.assertEqual(dec_data["status"], "approved")
        self.assertIn("audit_hash", dec_data)

    def test_patient_medical_history_endpoint(self):
        """Verify longitudinal Patient Medical History returns header, timeline, and 7 discrete clinical sections."""
        res = self.client.get("/api/dashboard/patient-history/DEMO-PT-001")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # Demographics Header
        self.assertEqual(data["case_id"], "DEMO-PT-001")
        self.assertEqual(data["patient_name"], "Sarah Jenkins")
        self.assertIn("timeline", data)
        self.assertGreaterEqual(len(data["timeline"]), 2)

        # 7 Required Sections
        self.assertIn("medical_conditions", data)
        self.assertIn("medication_history", data)
        self.assertIn("allergies", data)
        self.assertIn("laboratory_history", data)
        self.assertIn("clinical_assessments", data)
        self.assertIn("pharmacy_events", data)
        self.assertIn("audit_events", data)

        # Check lab item structure with linked lab report ID
        labs = data["laboratory_history"]
        self.assertGreaterEqual(len(labs), 1)
        self.assertTrue("test" in labs[0] or "test_name" in labs[0])
        self.assertIn("result", labs[0])
        self.assertIn("reference_range", labs[0])

    def test_admin_dashboard_data(self):
        """Verify Admin dashboard returns audit chain integrity, users, and gateway status."""
        self.current_user = self.db.query(User).filter(User.role == "Administrator").first()
        res = self.client.get("/api/dashboard/admin")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["role"], "Administrator")
        self.assertTrue(data["audit_chain"]["is_valid"])
        self.assertGreaterEqual(data["users_count"], 4)
        self.assertIn("sms_gateway", data)

    def test_clinical_console_html_rendering(self):
        """Verify /app renders complete HTML console with DDI branding and clinical workspaces."""
        res = self.client.get("/app")
        self.assertEqual(res.status_code, 200)
        html = res.text

        self.assertIn("DDI", html)
        self.assertIn("Medication Fulfillment", html)
        self.assertIn("Doctor", html)
        self.assertIn("Nurse", html)
        self.assertIn("Clinical Pharmacist", html)
        self.assertIn("Administrator", html)
        self.assertIn("view-doctor", html)
        self.assertIn("view-nurse", html)
        self.assertIn("view-pharmacist", html)
        self.assertIn("modal-medication-availability", html)
        self.assertIn("modal-substitution-request", html)
        self.assertIn("modal-patient-history", html)
        self.assertIn("modal-mar-detail", html)
        self.assertIn("modal-vital-detail", html)
        self.assertIn("Medication Administration Record", html)
        self.assertIn("Rapid Inpatient Lab / Vitals Ingestion", html)

    def test_switch_sms_provider_endpoint(self):
        """Verify switching SMS gateway via API works smoothly."""
        res = self.client.post("/auth/providers/switch", json={"provider_name": "msg91"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["provider_status"]["provider"], "msg91")

    def test_doctor_demo_evaluation_pipeline_with_xai_and_six_agents(self):
        """Verify Doctor evaluating scenario 1, 2, and 3 returns 6-agent trace, Explainable AI structure, clinical rationale, and actual audit SHA-256 hash."""
        for scenario_id in [1, 2, 3]:
            res = self.client.post(f"/demo/run/{scenario_id}")
            self.assertEqual(res.status_code, 200)
            data = res.json()

            # 1. Patient Context & Findings
            self.assertEqual(data["scenario"]["id"], scenario_id)
            self.assertIn("findings", data)
            self.assertGreaterEqual(len(data["findings"]), 1)

            # 2. Clinical Pharmacotherapy Rationale
            self.assertIn("clinical_rationale", data)
            self.assertTrue(len(data["clinical_rationale"]) > 20)

            # 3. Clinical Explainable AI (Ollama/Local Explicator)
            self.assertIn("explainable_ai", data)
            xai = data["explainable_ai"]
            self.assertTrue(xai["is_local_ai"])
            self.assertIn("why_flagged", xai)
            self.assertIn("mechanism", xai)
            self.assertIn("significance", xai)
            self.assertIn("evidence_considered", xai)
            self.assertIn("risk_reasoning", xai)
            self.assertIn("recommended_consideration", xai)

            # 4. Six Agents Execution Trace
            self.assertIn("six_agents_trace", data)
            self.assertEqual(len(data["six_agents_trace"]), 6)
            agent_names = [a["agent_name"] for a in data["six_agents_trace"]]
            self.assertEqual(agent_names, [
                "Knowledge Normalizer",
                "Rule Pack & Provenance",
                "Deterministic Risk Detector",
                "Safety Resolution Engine",
                "Explainable AI Explicator",
                "Cryptographic Audit Ledger",
            ])
            for a in data["six_agents_trace"]:
                self.assertEqual(a["status"], "Completed")
                self.assertTrue(bool(a["input"]))
                self.assertTrue(bool(a["output"]))

            # Agent 6 must contain an actual SHA-256 hash
            agent6 = data["six_agents_trace"][5]
            self.assertIn("Audit Block:", agent6["output"])
            self.assertIn("SHA-256 Cryptographic Hash Chain Valid", agent6["output"])

            # 5. Deterministic Verification & Cryptographic Audit
            self.assertIn("deterministic_verification", data)
            self.assertEqual(data["deterministic_verification"]["status"], "VERIFIED")
            self.assertIn("audit_event", data)
            self.assertTrue(data["audit_event"]["is_valid"])
            self.assertEqual(len(data["audit_event"]["current_hash"]), 64)


if __name__ == "__main__":
    unittest.main()



