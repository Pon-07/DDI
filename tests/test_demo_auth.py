import os
import tempfile
import unittest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from tests.test_client import LocalTestClient
from agents.audit.ledger import AuditLedger
from app.dependencies import get_audit_ledger
from app.main import app
from database.database import Base, get_db
from engine.auth.config import AuthConfig
from engine.auth.models import User, UserSession, init_auth_tables
from engine.auth.service import AuthService


class CookieClient(LocalTestClient):
    """LocalTestClient with automatic session cookie tracking."""

    def __init__(self, app_instance):
        super().__init__(app_instance)
        self.cookies = {}

    def request(self, method, path, json_data=None, data=None, params=None, headers=None):
        req_headers = dict(headers or {})
        if self.cookies and "cookie" not in req_headers:
            req_headers["cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        res = super().request(method, path, json_data=json_data, data=data, params=params, headers=req_headers)
        if "set-cookie" in res.headers:
            cookie_raw = res.headers["set-cookie"]
            first_part = cookie_raw.split(";")[0]
            if "=" in first_part:
                k, v = first_part.split("=", 1)
                k = k.strip()
                v = v.strip()
                if "max-age=0" in cookie_raw.lower() or not v:
                    self.cookies.pop(k, None)
                else:
                    self.cookies[k] = v
        return res


class TestDemoAuth(unittest.TestCase):
    """
    Focused test suite for hackathon demo authentication:
    - Server-side demo mode enforcement
    - Whitelist enforcement of the 4 synthetic personas
    - Active and demo-allowed account verification
    - Expiring session and cookie handling
    - Tamper-evident audit logging without secret leakage
    - Session validation and logout revocation
    - Privilege escalation prevention
    - Route backward compatibility
    """

    def setUp(self):
        # Create isolated in-memory DB
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        init_auth_tables(self.engine)

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        # Create isolated temp audit ledger
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = os.path.join(self.temp_dir.name, "auth_audit_test.db")
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

        # Dependency overrides for FastAPI app
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_audit_ledger] = lambda: self.audit_ledger
        self.client = CookieClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.temp_dir.cleanup()

    def test_demo_login_rejected_when_demo_mode_disabled(self):
        """1. Verify demo login is rejected with 403 when AEGIS_DEMO_MODE is false or unset."""
        os.environ["AEGIS_DEMO_MODE"] = "false"
        response = self.client.post(
            "/auth/demo-login",
            json={"persona_identifier": "doctor"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("server-side demo mode is disabled", response.json()["detail"].lower())

    def test_demo_personas_endpoint_reflects_mode(self):
        """Verify /auth/demo-personas returns personas only when enabled."""
        os.environ["AEGIS_DEMO_MODE"] = "false"
        res_disabled = self.client.get("/auth/demo-personas")
        self.assertEqual(res_disabled.status_code, 200)
        self.assertFalse(res_disabled.json()["demo_mode"])
        self.assertEqual(len(res_disabled.json()["personas"]), 0)

        os.environ["AEGIS_DEMO_MODE"] = "true"
        res_enabled = self.client.get("/auth/demo-personas")
        self.assertEqual(res_enabled.status_code, 200)
        self.assertTrue(res_enabled.json()["demo_mode"])
        self.assertEqual(len(res_enabled.json()["personas"]), 4)

    def test_demo_login_all_four_personas_success(self):
        """2. Verify only the four configured personas can log in successfully."""
        os.environ["AEGIS_DEMO_MODE"] = "true"

        personas = [
            ("doctor", "Demo Doctor", "doctor"),
            ("nurse", "Demo Nurse", "nurse"),
            ("pharmacist", "Demo Pharmacist", "pharmacist"),
            ("administrator", "Demo Administrator", "administrator"),
        ]

        for p_id, expected_name, expected_role in personas:
            res = self.client.post("/auth/demo-login", json={"persona_identifier": p_id})
            self.assertEqual(res.status_code, 200, f"Failed demo login for {p_id}: {res.text}")
            data = res.json()
            self.assertEqual(data["user"]["full_name"], expected_name)
            self.assertEqual(data["role"], expected_role)
            self.assertTrue(data["session_token"])
            # Check cookie was set
            self.assertIn("set-cookie", res.headers)
            self.assertIn("aegis_session", self.client.cookies)

    def test_demo_login_with_phone_number(self):
        """Verify login works with normalized phone number for configured persona."""
        os.environ["AEGIS_DEMO_MODE"] = "true"
        res = self.client.post("/auth/demo-login", json={"persona_identifier": "+15555550101"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["user"]["username"], "demo_doctor")

    def test_demo_login_rejects_arbitrary_or_unconfigured_identifiers(self):
        """3. Verify arbitrary phone numbers or usernames are rejected."""
        os.environ["AEGIS_DEMO_MODE"] = "true"

        invalid_identifiers = [
            "+15555559999",  # Unregistered phone
            "random_user",
            "root",
            "hacker",
            "evaluator",
        ]

        for bad_id in invalid_identifiers:
            res = self.client.post("/auth/demo-login", json={"persona_identifier": bad_id})
            self.assertEqual(res.status_code, 400)
            self.assertIn("Invalid demo persona", res.json()["detail"])

    def test_demo_login_rejects_inactive_user(self):
        """4. Verify deactivated user cannot log in even with valid demo persona."""
        os.environ["AEGIS_DEMO_MODE"] = "true"

        # Pre-seed inactive doctor
        deactivated = User(
            username="demo_doctor",
            full_name="Demo Doctor",
            phone_number="+15555550101",
            role="doctor",
            is_active=False,  # Inactive
            demo_mode_allowed=True,
        )
        self.db.add(deactivated)
        self.db.commit()

        res = self.client.post("/auth/demo-login", json={"persona_identifier": "doctor"})
        self.assertEqual(res.status_code, 403)
        self.assertIn("deactivated", res.json()["detail"].lower())

    def test_demo_login_rejects_demo_disallowed_user(self):
        """5. Verify user without demo_mode_allowed cannot log in through demo endpoint."""
        os.environ["AEGIS_DEMO_MODE"] = "true"

        # Pre-seed user with demo_mode_allowed = False
        disallowed = User(
            username="demo_nurse",
            full_name="Demo Nurse",
            phone_number="+15555550102",
            role="nurse",
            is_active=True,
            demo_mode_allowed=False,  # Not allowed
        )
        self.db.add(disallowed)
        self.db.commit()

        res = self.client.post("/auth/demo-login", json={"persona_identifier": "nurse"})
        self.assertEqual(res.status_code, 403)
        self.assertIn("not permitted for demo-mode", res.json()["detail"].lower())

    def test_successful_login_writes_audit_trail_without_secrets(self):
        """6. Verify successful login writes an audit event to AuditLedger without leaking tokens or secrets."""
        os.environ["AEGIS_DEMO_MODE"] = "true"

        res = self.client.post("/auth/demo-login", json={"persona_identifier": "pharmacist"})
        self.assertEqual(res.status_code, 200)
        token = res.json()["session_token"]

        # Inspect audit ledger records
        events = self.audit_ledger.get_events()
        self.assertGreater(len(events), 0)

        login_events = [e for e in events if e.event_type == "DEMO_LOGIN_SUCCESS"]
        self.assertEqual(len(login_events), 1)

        event = login_events[0]
        self.assertEqual(event.actor, "demo_pharmacist")
        self.assertEqual(event.payload.get("role"), "pharmacist")
        self.assertEqual(event.payload.get("persona_id"), "pharmacist")

        # Crucial security check: raw session token or secrets must NEVER appear in audit payload
        payload_str = str(event.payload)
        self.assertNotIn(token, payload_str)
        self.assertNotIn("session_token", event.payload)
        self.assertNotIn("password", payload_str)

        # Cryptographic chain remains valid
        verify_res = self.audit_ledger.verify_chain()
        self.assertTrue(verify_res.is_valid)

    def test_logout_revokes_session_and_clears_cookie(self):
        """7. Verify logout invalidates session in DB, clears cookie, and logs audit record."""
        os.environ["AEGIS_DEMO_MODE"] = "true"

        login_res = self.client.post("/auth/demo-login", json={"persona_identifier": "doctor"})
        self.assertEqual(login_res.status_code, 200)

        # /auth/me should succeed with cookie
        me_res = self.client.get("/auth/me")
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["username"], "demo_doctor")

        # Logout
        logout_res = self.client.post("/auth/logout")
        self.assertEqual(logout_res.status_code, 200)
        self.assertEqual(logout_res.json()["status"], "logged_out")

        # /auth/me should now fail with 401
        me_after_logout = self.client.get("/auth/me")
        self.assertEqual(me_after_logout.status_code, 401)

        # Verify session is deactivated in DB
        db_session = self.db.query(UserSession).filter_by(is_active=True).first()
        self.assertIsNone(db_session)

        # Verify audit logout event
        logout_events = [e for e in self.audit_ledger.get_events() if e.event_type == "AUTH_LOGOUT_SUCCESS"]
        self.assertEqual(len(logout_events), 1)

    def test_privilege_escalation_prevention(self):
        """8. Verify demo login cannot grant Administrator privileges to non-admin roles."""
        os.environ["AEGIS_DEMO_MODE"] = "true"

        # Log in as doctor
        res = self.client.post("/auth/demo-login", json={"persona_identifier": "doctor"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["role"], "doctor")

        # Doctor attempts to escalate role to administrator
        escalate_res = self.client.post("/auth/switch-role", json={"new_role": "administrator"})
        self.assertEqual(escalate_res.status_code, 403)
        self.assertIn("Privilege escalation prohibited", escalate_res.json()["detail"])

    def test_otp_and_totp_routes_preserved(self):
        """9. Verify OTP/TOTP routes remain intact for compatibility."""
        os.environ["AEGIS_DEMO_MODE"] = "true"

        # Log in to get session
        self.client.post("/auth/demo-login", json={"persona_identifier": "doctor"})

        otp_req = self.client.post("/auth/otp/request", json={"phone_number": "+15555550101"})
        self.assertEqual(otp_req.status_code, 200)
        self.assertEqual(otp_req.json()["status"], "otp_sent")

        otp_ver = self.client.post("/auth/otp/verify", json={"phone_number": "+15555550101", "otp_code": "123456"})
        self.assertEqual(otp_ver.status_code, 200)
        self.assertEqual(otp_ver.json()["status"], "verified")

        totp_setup = self.client.get("/auth/totp/setup")
        self.assertEqual(totp_setup.status_code, 200)
        self.assertTrue(totp_setup.json()["secret"])

        totp_ver = self.client.post("/auth/totp/verify", json={"totp_code": "123456"})
        self.assertEqual(totp_ver.status_code, 200)
        self.assertEqual(totp_ver.json()["status"], "totp_verified")


if __name__ == "__main__":
    unittest.main()
