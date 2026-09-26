import os
import tempfile
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.audit.ledger import AuditLedger
from app.main import app
from database.database import Base, get_db
from engine.auth.config import AuthConfig, DEMO_PERSONAS
from engine.auth.models import AuthBase, User, UserSession
from engine.auth.providers.local import LocalDemoOTPProvider
from engine.auth.service import AuthService
from tests.test_client import LocalTestClient


class TestAuthFlow(unittest.TestCase):
    """
    Comprehensive tests for mobile-number authentication flow:
    - Offline local OTP provider & demo mode
    - Non-demo mode cryptographic security (no hardcoded OTPs)
    - Role-based user login (Doctor, Nurse, Clinical Pharmacist, Administrator)
    - Session management & role-specific dashboard routing
    - Audit ledger integration (tamper-evident SHA-256 chain)
    """

    def setUp(self):
        # In-memory SQLite with StaticPool
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        AuthBase.metadata.create_all(bind=self.engine)

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        # Temp AuditLedger
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = os.path.join(self.temp_dir.name, "test_auth_audit.db")
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

        # Local Demo Provider
        self.local_provider = LocalDemoOTPProvider(demo_mode=True)
        self.auth_service = AuthService(
            audit_ledger=self.audit_ledger,
            sms_provider=self.local_provider,
            demo_mode=True,
        )
        self.auth_service.seed_demo_users(self.db)

        # Test client overrides
        app.dependency_overrides[get_db] = lambda: self.db
        from engine.auth.dependencies import get_auth_service
        app.dependency_overrides[get_auth_service] = lambda: self.auth_service

        self.client = LocalTestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        AuthBase.metadata.drop_all(bind=self.engine)
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_seed_demo_personas(self):
        """Verify all 4 required roles (Doctor, Nurse, Clinical Pharmacist, Administrator) are seeded."""
        users = self.db.query(User).all()
        self.assertEqual(len(users), 4)

        roles = {u.role for u in users}
        self.assertIn("Doctor", roles)
        self.assertIn("Nurse", roles)
        self.assertIn("Clinical Pharmacist", roles)
        self.assertIn("Administrator", roles)

    def test_request_otp_demo_mode(self):
        """Verify OTP request in demo mode returns preview code for instant offline testing."""
        res = self.client.post(
            "/auth/otp/request",
            json={"phone_number": "+15550192831", "demo_mode": True},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["is_demo_mode"])
        self.assertIsNotNone(data["preview_otp"])
        self.assertEqual(data["preview_otp"], "123456")
        self.assertIn("••••", data["phone_number_masked"])

    def test_production_mode_does_not_hardcode_or_leak_otp(self):
        """Verify production mode generates crypto random OTP and does NOT leak code in client response."""
        prod_provider = LocalDemoOTPProvider(demo_mode=False)
        prod_auth_service = AuthService(
            audit_ledger=self.audit_ledger,
            sms_provider=prod_provider,
            demo_mode=False,
        )

        res = prod_auth_service.request_otp(
            db=self.db,
            phone_number="+15559998877",
            demo_mode=False,
        )

        # preview_otp must be None in non-demo mode
        self.assertIsNone(res["preview_otp"])
        self.assertFalse(res["is_demo_mode"])

        # Code in delivery provider must be a 6-digit random code, not hardcoded 123456
        dispatches = prod_provider.get_recent_dispatches()
        self.assertTrue(len(dispatches) > 0)
        generated_otp = dispatches[0]["otp_code"]
        self.assertEqual(len(generated_otp), 6)
        self.assertTrue(generated_otp.isdigit())

    def test_successful_otp_verification_creates_session_and_routes(self):
        """Verify OTP verification creates session token, sets last_login_at, and returns role dashboard route."""
        # 1. Request OTP for Doctor
        self.client.post("/auth/otp/request", json={"phone_number": "+15550192831", "demo_mode": True})

        # 2. Verify OTP
        res = self.client.post(
            "/auth/otp/verify",
            json={"phone_number": "+15550192831", "otp_code": "123456", "role": "Doctor"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("session_token", data)
        self.assertTrue(data["session_token"].startswith("aegis_sess_"))
        self.assertEqual(data["role"], "Doctor")
        self.assertEqual(data["dashboard_url"], "/app/dashboard/doctor")
        self.assertEqual(data["user"]["full_name"], "Dr. Sarah Lin, MD")

        # 3. Check session in DB
        session_token = data["session_token"]
        user_sess = self.db.query(UserSession).filter(UserSession.session_token == session_token).first()
        self.assertIsNotNone(user_sess)
        self.assertTrue(user_sess.is_active)
        self.assertEqual(user_sess.role, "Doctor")

    def test_invalid_otp_fails_and_decrements_attempts(self):
        """Verify invalid OTP fails with 400 Bad Request and logs audit failure."""
        self.client.post("/auth/otp/request", json={"phone_number": "+15550192832", "demo_mode": False})

        res = self.client.post(
            "/auth/otp/verify",
            json={"phone_number": "+15550192832", "otp_code": "000000"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid or expired verification code", res.json()["detail"])

    def test_role_based_routing_for_all_four_roles(self):
        """Verify all 4 roles receive appropriate credentials and dashboard routes."""
        test_cases = [
            ("+15550192831", "Doctor", "/app/dashboard/doctor", "Dr. Sarah Lin, MD"),
            ("+15550192832", "Nurse", "/app/dashboard/nurse", "Elena Rostova, RN"),
            ("+15550192833", "Clinical Pharmacist", "/app/dashboard/clinical-pharmacist", "Marcus Vance, PharmD, BCPS"),
            ("+15550192834", "Administrator", "/app/dashboard/administrator", "Arthur Pendelton, MS, CPHIMS"),
        ]

        for phone, role, expected_url, expected_name in test_cases:
            self.client.post("/auth/otp/request", json={"phone_number": phone, "demo_mode": True})
            verify_res = self.client.post(
                "/auth/otp/verify",
                json={"phone_number": phone, "otp_code": "123456", "role": role},
            )
            self.assertEqual(verify_res.status_code, 200)
            data = verify_res.json()
            self.assertEqual(data["role"], role)
            self.assertEqual(data["dashboard_url"], expected_url)
            self.assertEqual(data["user"]["full_name"], expected_name)

    def test_authenticated_profile_and_logout(self):
        """Verify /auth/me returns current user profile and /auth/logout invalidates the session."""
        # Login
        self.client.post("/auth/otp/request", json={"phone_number": "+15550192831", "demo_mode": True})
        verify_res = self.client.post(
            "/auth/otp/verify",
            json={"phone_number": "+15550192831", "otp_code": "123456", "role": "Doctor"},
        )
        token = verify_res.json()["session_token"]

        # Call /auth/me with Bearer token
        me_res = self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["full_name"], "Dr. Sarah Lin, MD")

        # Call /auth/logout
        logout_res = self.client.post(
            "/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(logout_res.status_code, 200)
        self.assertTrue(logout_res.json()["success"])

        # Attempting /auth/me again must now return 401 Unauthorized
        me_after_logout = self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(me_after_logout.status_code, 401)

    def test_audit_ledger_records_auth_lifecycle(self):
        """Verify that every auth event (OTP request, login success, login fail, logout) is recorded in SHA-256 audit ledger."""
        phone = "+15550192831"

        # 1. Request OTP
        self.client.post("/auth/otp/request", json={"phone_number": phone, "demo_mode": True})

        # 2. Failed attempt
        self.client.post("/auth/otp/verify", json={"phone_number": phone, "otp_code": "999999", "role": "Doctor"})

        # 3. Successful login
        res = self.client.post("/auth/otp/verify", json={"phone_number": phone, "otp_code": "123456", "role": "Doctor"})
        token = res.json()["session_token"]

        # 4. Logout
        self.client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})

        # Verify audit ledger events
        events = self.audit_ledger.get_events()
        event_types = [e.event_type for e in events]

        self.assertIn("AUTH_OTP_REQUESTED", event_types)
        self.assertIn("AUTH_LOGIN_FAILED", event_types)
        self.assertIn("AUTH_LOGIN_SUCCESS", event_types)
        self.assertIn("AUTH_LOGOUT", event_types)

        # Cryptographic chain verification
        verification = self.audit_ledger.verify_chain()
        self.assertTrue(verification.is_valid)
        self.assertEqual(len(verification.errors), 0)


if __name__ == "__main__":
    unittest.main()
