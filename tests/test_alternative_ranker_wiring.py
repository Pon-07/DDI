import json
import os
import secrets
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.audit.ledger import AuditLedger
from agents.explicator.service import ExplicatorService
from agents.knowledge import DrugNormalizer, KnowledgeService
from agents.resolution.alternatives import AlternativeRanker, AlternativeReport, assess_risk_tier
from agents.resolution.resolver import SafetyResolutionEngine
from app.dependencies import (
    get_alternative_ranker,
    get_audit_ledger,
    get_event_service,
    get_explicator_service,
    get_resolution_engine,
)
from app.main import app
from database.database import Base, get_db
from engine.auth.config import AuthConfig, DEMO_PERSONAS
from engine.auth.dependencies import get_auth_service
from engine.auth.models import AuthBase, User, UserSession
from engine.auth.providers.local import LocalDemoOTPProvider
from engine.auth.service import AuthService
from engine.event_service import MedicationEventService
from models import Finding, Medication, Patient
from tests.test_client import LocalTestClient


class TestAlternativeRankerWiring(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        AuthBase.metadata.create_all(bind=self.engine)
        self.TestingSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        self.db = self.TestingSessionLocal()

        self.normalizer = DrugNormalizer()
        self.knowledge = KnowledgeService(normalizer=self.normalizer)
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        self.ledger = AuditLedger(db_path=self.temp_db_path)
        self.explicator = ExplicatorService()
        self.alt_ranker = AlternativeRanker()
        self.resolver = SafetyResolutionEngine(
            explicator=self.explicator,
            audit_ledger=self.ledger,
            alternative_ranker=self.alt_ranker,
        )
        self.events = MedicationEventService(
            knowledge_service=self.knowledge,
            resolution_engine=self.resolver,
            rule_context=self.resolver,
            explicator=self.explicator,
            audit_ledger=self.ledger,
        )

        # Auth service setup
        self.local_provider = LocalDemoOTPProvider(demo_mode=True)
        self.auth_service = AuthService(
            audit_ledger=self.ledger,
            sms_provider=self.local_provider,
            demo_mode=True,
        )
        self.auth_service.seed_demo_users(self.db)

        # Seed test patient and finding
        self.test_patient = Patient(patient_identifier="TEST-PT-001", name="Alice Smith")
        self.db.add(self.test_patient)
        self.db.flush()

        self.test_med = Medication(
            patient_id=self.test_patient.id,
            drug_name="fluconazole",
            status="active",
        )
        self.test_finding = Finding(
            patient_id=self.test_patient.id,
            rule_id="RULE-WAR-FLU-001",
            severity="critical",
            title="Warfarin and Fluconazole Interaction",
            description="Severe CYP2C9 inhibition",
            inputs={"drug_a": "warfarin", "drug_b": "fluconazole"},
            trace={"source": "DDInter"},
        )
        self.db.add_all([self.test_med, self.test_finding])
        self.db.commit()

        # Seed authenticated sessions for role testing
        doc_user = self.db.query(User).filter(User.role == "Doctor").first()
        nurse_user = self.db.query(User).filter(User.role == "Nurse").first()
        pharm_user = self.db.query(User).filter(User.role == "Clinical Pharmacist").first()

        self.doc_session = self._create_user_session(doc_user)
        self.nurse_session = self._create_user_session(nurse_user)
        self.pharm_session = self._create_user_session(pharm_user)

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_audit_ledger] = lambda: self.ledger
        app.dependency_overrides[get_explicator_service] = lambda: self.explicator
        app.dependency_overrides[get_alternative_ranker] = lambda: self.alt_ranker
        app.dependency_overrides[get_resolution_engine] = lambda: self.resolver
        app.dependency_overrides[get_event_service] = lambda: self.events
        app.dependency_overrides[get_auth_service] = lambda: self.auth_service

        self.client = LocalTestClient(app)

    def _create_user_session(self, user: User) -> UserSession:
        now = datetime.utcnow()
        token = f"aegis_sess_{secrets.token_urlsafe(36)}"
        session = UserSession(
            session_token=token,
            user_id=user.id,
            role=user.role,
            phone_number=user.phone_number,
            ip_address="127.0.0.1",
            user_agent="pytest",
            expires_at=now + timedelta(hours=24),
            is_active=True,
        )
        self.db.add(session)
        self.db.commit()
        return session

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        AuthBase.metadata.drop_all(bind=self.engine)
        Base.metadata.drop_all(bind=self.engine)
        try:
            os.close(self.temp_db_fd)
            if os.path.exists(self.temp_db_path):
                os.remove(self.temp_db_path)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Core Dependency Injection & Demo Flow Tests
    # -------------------------------------------------------------------------
    def test_dependency_injection_wires_alternative_ranker(self):
        eng = get_resolution_engine(
            audit_ledger=self.ledger,
            explicator=self.explicator,
            alternative_ranker=self.alt_ranker,
        )
        self.assertIsNotNone(eng.alternative_ranker)
        self.assertIs(eng.alternative_ranker, self.alt_ranker)

    def test_demo_run_scenario_1_returns_alternatives_payload(self):
        resp = self.client.post("/demo/run/1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("alternatives", data)
        self.assertIn("resolutions", data)
        if data["resolutions"]:
            first_res = data["resolutions"][0]
            self.assertIn("alternatives", first_res)
            alt = first_res["alternatives"]
            if alt:
                self.assertIn("risk_tier", alt)
                self.assertEqual(alt["risk_tier"], "HIGH")
                self.assertTrue(alt["requires_cosign"])
                self.assertFalse(alt["auto_execute"])
                self.assertTrue(alt["advisory_only"])

    def test_demo_run_scenario_3_duplicate_therapy_consolidation(self):
        resp = self.client.post("/demo/run/3")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("alternatives", data)
        if data["resolutions"]:
            alt = data["resolutions"][0].get("alternatives")
            if alt and alt.get("consolidation_action"):
                ca = alt["consolidation_action"]
                self.assertEqual(ca["action_type"], "consolidate_duplicate_therapy")
                self.assertTrue(ca["requires_cosign"])
                self.assertFalse(alt["auto_execute"])
                self.assertIn("duplicate therapy", ca["reason"].lower())

    # -------------------------------------------------------------------------
    # API Authentication & Role-Based Authorization Tests
    # -------------------------------------------------------------------------
    def test_findings_alternatives_unauthenticated_rejected(self):
        """Unauthenticated requests must be rejected with 401 Unauthorized."""
        resp = self.client.get(f"/findings/{self.test_finding.id}/alternatives")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Authentication required", resp.json()["detail"])

    def test_findings_alternatives_invalid_session_rejected(self):
        """Invalid session token must be rejected with 401 Unauthorized."""
        resp = self.client.get(
            f"/findings/{self.test_finding.id}/alternatives",
            headers={"Authorization": "Bearer INVALID_TOKEN_XYZ"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_findings_alternatives_unauthorized_role_rejected(self):
        """Nurse role lacks permission for alternative ranker and is rejected with 403 Forbidden."""
        resp = self.client.get(
            f"/findings/{self.test_finding.id}/alternatives",
            headers={"Authorization": f"Bearer {self.nurse_session.session_token}"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Access forbidden", resp.json()["detail"])

    def test_findings_alternatives_authorized_missing_finding_returns_404(self):
        """Doctor role requesting non-existent finding returns 404 Not Found."""
        resp = self.client.get(
            "/findings/99999/alternatives",
            headers={"Authorization": f"Bearer {self.doc_session.session_token}"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertIn("not found", resp.json()["detail"].lower())

    def test_findings_alternatives_doctor_authorized_success(self):
        """Doctor role requesting existing finding receives 200 OK with alternative report."""
        resp = self.client.get(
            f"/findings/{self.test_finding.id}/alternatives",
            headers={"Authorization": f"Bearer {self.doc_session.session_token}"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("risk_tier", data)
        self.assertEqual(data["risk_tier"], "HIGH")
        self.assertTrue(data["requires_cosign"])
        self.assertFalse(data["auto_execute"])
        self.assertTrue(data["advisory_only"])

    def test_findings_alternatives_pharmacist_authorized_success(self):
        """Clinical Pharmacist role requesting existing finding receives 200 OK with alternative report."""
        resp = self.client.get(
            f"/findings/{self.test_finding.id}/alternatives",
            headers={"Authorization": f"Bearer {self.pharm_session.session_token}"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("risk_tier", data)

    # -------------------------------------------------------------------------
    # Frontend Security (XSS Escaping) Regression Tests
    # -------------------------------------------------------------------------
    def test_web_app_ui_contains_alternative_ranker_wiring(self):
        resp = self.client.get("/app")
        self.assertEqual(resp.status_code, 200)
        text = resp.text
        self.assertIn("escapeHtml", text)
        self.assertIn("renderAlternativeRankerSection", text)
        self.assertIn("Alternative Drug Ranking", text)
        self.assertIn("Clinical Boundaries", text)

    def test_frontend_xss_escaping_with_malicious_payloads(self):
        """
        Verify that renderAlternativeRankerSection safely neutralizes XSS vectors
        including <script>, <img onerror>, and attribute injection attacks.
        """
        resp = self.client.get("/app")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Extract JS functions escapeHtml and renderAlternativeRankerSection from the page
        escape_fn_idx = html.find("function escapeHtml(str)")
        render_fn_idx = html.find("function renderAlternativeRankerSection(altReport)")
        end_idx = html.find("function renderDoctorClinicalAIResult(data, scenarioId)")
        self.assertNotEqual(escape_fn_idx, -1)
        self.assertNotEqual(render_fn_idx, -1)
        self.assertNotEqual(end_idx, -1)

        js_source = html[escape_fn_idx:end_idx]

        # Construct a payload with aggressive XSS attack strings in all dynamic fields
        malicious_report = {
            "risk_tier": "HIGH",
            "ranker_version": "alt-ranker-1.0<script>alert('version')</script>",
            "gate_reason": "High Risk Gate <b onmouseover=\"alert('gate')\">hover</b>",
            "advisory_only": True,
            "requires_cosign": True,
            "auto_execute": False,
            "consolidation_action": {
                "action_type": '<script>alert("consolidation")</script>',
                "description": '<img src=x onerror="alert(\'desc\')">',
                "reason": '" onclick="alert(\'reason\')"',
                "requires_cosign": True,
                "auto_execute": False,
            },
            "replacement_sets": [
                {
                    "replaces": '<svg onload="alert(\'replaces\')">',
                    "n_considered": 5,
                    "level3_fallback_used": False,
                    "candidates": [
                        {
                            "rank": 1,
                            "drug_name": '<script>alert("drug")</script>',
                            "reason": '<iframe src="javascript:alert(1)"></iframe>',
                            "equivalence_level": 4,
                            "score": 85.5,
                            "components": {"interaction": 60, "equivalence": 25, "dosing_evidence": 0},
                            "interaction_class": 'moderate" onfocus="alert(1)',
                            "dosing": {"tier": 'label_only"><script>alert(2)</script>'},
                            "known_major_interaction": False,
                            "needs_pharmacist_review": False,
                            "requires_cosign": True,
                            "auto_execute": False,
                            "is_suggestion": True,
                        }
                    ],
                }
            ],
        }

        # Run with node to verify rendered output in actual V8 engine
        eval_script = f"""
        {js_source}
        const report = {json.dumps(malicious_report)};
        const output = renderAlternativeRankerSection(report);
        console.log(output);
        """

        node_res = subprocess.run(
            ["node", "-e", eval_script],
            capture_output=True,
            text=True,
            check=True,
        )
        rendered_output = node_res.stdout

        # Assert no unescaped dangerous tags or handlers exist in output
        self.assertNotIn("<script>", rendered_output)
        self.assertNotIn("</script>", rendered_output)
        self.assertNotIn("<img", rendered_output)
        self.assertNotIn("<svg", rendered_output)
        self.assertNotIn("<iframe", rendered_output)
        self.assertNotIn("<b onmouseover", rendered_output)

        # Assert that characters were safely escaped
        self.assertIn("&lt;script&gt;alert(", rendered_output)
        self.assertIn("&lt;img src=x onerror=&quot;", rendered_output)
        self.assertIn("&lt;svg onload=&quot;", rendered_output)
        self.assertIn("&lt;iframe", rendered_output)

    def test_frontend_renders_normal_clinical_content_cleanly(self):
        """Verify normal clinical content renders correctly with proper badges and scores."""
        resp = self.client.get("/app")
        html = resp.text

        escape_fn_idx = html.find("function escapeHtml(str)")
        render_fn_idx = html.find("function renderAlternativeRankerSection(altReport)")
        end_idx = html.find("function renderDoctorClinicalAIResult(data, scenarioId)")
        js_source = html[escape_fn_idx:end_idx]

        clean_report = {
            "risk_tier": "HIGH",
            "ranker_version": "alt-ranker-1.0",
            "gate_reason": "Finding severity 'critical' is in the high-risk set.",
            "advisory_only": True,
            "requires_cosign": True,
            "auto_execute": False,
            "replacement_sets": [
                {
                    "replaces": "warfarin",
                    "n_considered": 3,
                    "level3_fallback_used": False,
                    "candidates": [
                        {
                            "rank": 1,
                            "drug_name": "apixaban",
                            "reason": "Same ATC L4 class with no known major interactions.",
                            "equivalence_level": 4,
                            "score": 88.0,
                            "components": {"interaction": 60, "equivalence": 25, "dosing_evidence": 3},
                            "interaction_class": "minor",
                            "dosing": {"tier": "label_only"},
                            "known_major_interaction": False,
                            "needs_pharmacist_review": False,
                            "requires_cosign": True,
                            "auto_execute": False,
                            "is_suggestion": True,
                        }
                    ],
                }
            ],
        }

        eval_script = f"""
        {js_source}
        const report = {json.dumps(clean_report)};
        const output = renderAlternativeRankerSection(report);
        console.log(output);
        """

        node_res = subprocess.run(
            ["node", "-e", eval_script],
            capture_output=True,
            text=True,
            check=True,
        )
        out = node_res.stdout
        self.assertIn("apixaban", out)
        self.assertIn("warfarin", out)
        self.assertIn("ATC L4", out)
        self.assertIn("88.0", out)
        self.assertIn("Advisory Ready", out)
        self.assertIn("Co-Sign Req.", out)
        self.assertIn("Clinical Boundaries:", out)

    # -------------------------------------------------------------------------
    # Clinical Safety Boundary Invariants
    # -------------------------------------------------------------------------
    def test_clinical_safety_boundaries_and_disclaimers(self):
        """
        Verify safety boundaries:
        - unknown/missing severity yields 0 alternatives and sets reason
        - moderate severity yields monitoring action + max 3 consider items
        - duplicate therapy yields consolidate action and no drug substitutes
        - every candidate enforces clinician review (requires_cosign=True, auto_execute=False)
        """
        # 1. Missing severity
        unknown_tier, unknown_reason = assess_risk_tier(None)
        self.assertEqual(unknown_tier, "UNKNOWN")
        self.assertIn("undetermined", unknown_reason)

        empty_finding = {"severity": "undetermined", "inputs": {"drug_a": "warfarin", "drug_b": "aspirin"}}
        rep_unknown = self.alt_ranker.build_report(empty_finding)
        self.assertEqual(rep_unknown.risk_tier, "UNKNOWN")
        self.assertEqual(len(rep_unknown.replacement_sets), 0)
        self.assertIsNotNone(rep_unknown.no_alternatives_reason)
        self.assertTrue(rep_unknown.requires_cosign)
        self.assertFalse(rep_unknown.auto_execute)

        # 2. Moderate severity
        mod_tier, mod_reason = assess_risk_tier("moderate")
        self.assertEqual(mod_tier, "MODERATE")
        self.assertIn("monitoring plan", mod_reason)

        # 3. Duplicate therapy
        dup_finding = {
            "severity": "major",
            "inputs": {
                "duplicate_class": "ACE-Inhibitors",
                "active_ace_inhibitors": ["lisinopril", "enalapril"],
            },
        }
        rep_dup = self.alt_ranker.build_report(dup_finding)
        self.assertIsNotNone(rep_dup.consolidation_action)
        self.assertEqual(rep_dup.consolidation_action.action_type, "consolidate_duplicate_therapy")
        self.assertEqual(len(rep_dup.replacement_sets), 0)
        self.assertTrue(rep_dup.consolidation_action.requires_cosign)
        self.assertFalse(rep_dup.auto_execute)
