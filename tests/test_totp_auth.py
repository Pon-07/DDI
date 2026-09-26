import os
import tempfile
import time
import unittest
import pyotp
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


class TestTOTPAuth(unittest.TestCase):
    """
    Comprehensive tests for Offline RFC 6238 TOTP Authentication in MICROMEDX:
    - Base32 secret generation & uniqueness per user
    - Offline QR code generation (Base64 PNG data URI)
    - otpauth:// URI formatting (6 digits, 30s interval, issuer="MICROMEDX")
    - 2-step enrollment flow (setup -> verify before activating)
    - Successful TOTP login for all 4 clinical personas (Doctor, Nurse, Pharmacist, Admin)
    - Rejection of invalid codes and expired/drifted codes
    - Replay protection (prevent reusing same code in same 30s timestep)
    - Tamper-evident AuditLedger recording (AUTH_TOTP_*)
    - Role-based dashboard routing and session invalidation on logout
    - Verification that secrets are never leaked in login responses
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

        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = os.path.join(self.temp_dir.name, "test_totp_audit.db")
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

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

    def test_demo_personas_seeded_with_distinct_totp_secrets(self):
        """Verify all 4 demo personas have unique pre-configured Base32 secrets."""
        users = self.db.query(User).all()
        self.assertEqual(len(users), 4)

        secrets = [u.totp_secret for u in users]
        # All secrets must be present, 32 characters, and unique
        self.assertEqual(len(secrets), 4)
        self.assertEqual(len(set(secrets)), 4)
        for s in secrets:
            self.assertIsNotNone(s)
            self.assertEqual(len(s), 32)
            # Verify secret is valid Base32
            totp = pyotp.TOTP(s)
            code = totp.now()
            self.assertEqual(len(code), 6)
            self.assertTrue(code.isdigit())

    def test_totp_enrollment_setup_generates_qr_and_secret(self):
        """Verify setup_totp_enrollment produces Base32 secret, formatted key, otpauth URI, and QR image."""
        phone = "+15550998877"
        res = self.client.post(
            "/auth/totp/enroll/setup",
            json={"phone_number": phone, "role_hint": "Doctor", "full_name": "Dr. Test Clinician"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["phone_number"], phone)
        self.assertEqual(len(data["secret_base32"]), 32)
        self.assertIn(" ", data["secret_formatted"])
        self.assertTrue(data["otpauth_uri"].startswith("otpauth://totp/MICROMEDX:"))
        self.assertIn("secret=" + data["secret_base32"], data["otpauth_uri"])
        self.assertIn("issuer=MICROMEDX", data["otpauth_uri"])
        self.assertTrue(data["qr_code_data_uri"].startswith("data:image/png;base64,"))

        # Check DB state: User exists but totp_enabled is still False until verified
        user = self.db.query(User).filter(User.phone_number == phone).first()
        self.assertIsNotNone(user)
        self.assertFalse(user.totp_enabled)
        self.assertEqual(user.totp_secret, data["secret_base32"])

    def test_totp_enrollment_verification_lifecycle(self):
        """Verify 2-step enrollment: secret generated -> 6-digit code submitted -> totp_enabled becomes True."""
        phone = "+15550998877"
        # 1. Setup
        setup_res = self.client.post(
            "/auth/totp/enroll/setup",
            json={"phone_number": phone, "role_hint": "Nurse", "full_name": "Nurse Testing"},
        )
        self.assertEqual(setup_res.status_code, 200)
        secret = setup_res.json()["secret_base32"]

        # 2. Reject wrong code
        bad_verify = self.client.post(
            "/auth/totp/enroll/verify",
            json={"phone_number": phone, "totp_code": "000000"},
        )
        self.assertEqual(bad_verify.status_code, 400)
        self.assertIn("Invalid 6-digit code", bad_verify.json()["detail"])

        # 3. Generate correct RFC 6238 code
        totp = pyotp.TOTP(secret, interval=30, digits=6)
        valid_code = totp.now()

        # 4. Verify enrollment
        good_verify = self.client.post(
            "/auth/totp/enroll/verify",
            json={"phone_number": phone, "totp_code": valid_code},
        )
        self.assertEqual(good_verify.status_code, 200)
        self.assertTrue(good_verify.json()["success"])

        # 5. Check user in DB is enabled
        user = self.db.query(User).filter(User.phone_number == phone).first()
        self.assertTrue(user.totp_enabled)
        self.assertIsNotNone(user.totp_last_verified_at)

    def test_totp_login_for_all_four_roles(self):
        """Verify Doctor, Nurse, Pharmacist, and Admin can authenticate via TOTP and reach their dashboards."""
        role_map = {
            "+15550192831": ("/app/dashboard/doctor", "Doctor"),
            "+15550192832": ("/app/dashboard/nurse", "Nurse"),
            "+15550192833": ("/app/dashboard/clinical-pharmacist", "Clinical Pharmacist"),
            "+15550192834": ("/app/dashboard/administrator", "Administrator"),
        }

        for phone, (expected_url, expected_role) in role_map.items():
            user = self.db.query(User).filter(User.phone_number == phone).first()
            self.assertIsNotNone(user)
            self.assertTrue(user.totp_enabled)

            # Generate standard TOTP code from user's secret
            totp = pyotp.TOTP(user.totp_secret, interval=30, digits=6)
            code = totp.now()

            # Submit TOTP login request
            res = self.client.post(
                "/auth/totp/verify",
                json={"phone_number": phone, "totp_code": code},
            )
            self.assertEqual(res.status_code, 200, f"Login failed for {phone} ({expected_role}): {res.text}")
            data = res.json()

            self.assertTrue(data["success"])
            self.assertEqual(data["auth_method"], "totp")
            self.assertEqual(data["role"], expected_role)
            self.assertEqual(data["dashboard_url"], expected_url)
            self.assertIsNotNone(data["session_token"])

            # Verify secret is NEVER exposed in the login API response
            self.assertNotIn("totp_secret", str(data))
            self.assertNotIn("secret", str(data))

    def test_totp_login_invalid_code_rejection(self):
        """Verify invalid TOTP codes are rejected and do not create a session."""
        res = self.client.post(
            "/auth/totp/verify",
            json={"phone_number": "+15550192831", "totp_code": "999999"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid or expired", res.json()["detail"])

    def test_totp_replay_protection(self):
        """Verify the exact same TOTP code cannot be used twice in the same 30s timestep window."""
        phone = "+15550192831"
        user = self.db.query(User).filter(User.phone_number == phone).first()
        totp = pyotp.TOTP(user.totp_secret, interval=30, digits=6)
        code = totp.now()

        # First verification succeeds
        res1 = self.client.post(
            "/auth/totp/verify",
            json={"phone_number": phone, "totp_code": code},
        )
        self.assertEqual(res1.status_code, 200)

        # Immediate second verification with SAME code in same window must be rejected (replay attack)
        res2 = self.client.post(
            "/auth/totp/verify",
            json={"phone_number": phone, "totp_code": code},
        )
        self.assertEqual(res2.status_code, 400)
        self.assertIn("already been used", res2.json()["detail"].lower())

    def test_totp_expired_code_rejection(self):
        """Verify codes generated with timestamps from 5 minutes ago are rejected."""
        phone = "+15550192831"
        user = self.db.query(User).filter(User.phone_number == phone).first()
        totp = pyotp.TOTP(user.totp_secret, interval=30, digits=6)

        # Generate code from 300 seconds ago (10 intervals in past)
        past_time = int(time.time()) - 300
        expired_code = totp.at(past_time)

        res = self.client.post(
            "/auth/totp/verify",
            json={"phone_number": phone, "totp_code": expired_code},
        )
        self.assertEqual(res.status_code, 400)

    def test_totp_status_and_demo_endpoints(self):
        """Verify status and presentation helper endpoints."""
        # Status endpoint
        res_status = self.client.get("/auth/totp/status/+15550192831")
        self.assertEqual(res_status.status_code, 200)
        self.assertTrue(res_status.json()["totp_enabled"])
        self.assertEqual(res_status.json()["user_role"], "Doctor")

        # Demo helper endpoint
        res_demo = self.client.get("/auth/totp/demo-code/+15550192831")
        self.assertEqual(res_demo.status_code, 200)
        data = res_demo.json()
        self.assertEqual(len(data["code"]), 6)
        self.assertTrue(0 <= data["remaining_seconds"] <= 30)

    def test_totp_audit_ledger_recording(self):
        """Verify tamper-evident SHA-256 cryptographic audit records are generated for TOTP events."""
        phone = "+15550192831"
        user = self.db.query(User).filter(User.phone_number == phone).first()
        totp = pyotp.TOTP(user.totp_secret)
        code = totp.now()

        # Success login
        self.client.post("/auth/totp/verify", json={"phone_number": phone, "totp_code": code})
        # Failed login
        self.client.post("/auth/totp/verify", json={"phone_number": phone, "totp_code": "000000"})

        # Inspect audit ledger
        records = self.audit_ledger.get_events(limit=20)
        event_types = [r.event_type for r in records]

        self.assertIn("AUTH_TOTP_SUCCESS", event_types)
        self.assertIn("AUTH_TOTP_FAILED", event_types)

        # Verify audit chain integrity
        result = self.audit_ledger.verify_chain()
        self.assertTrue(result.is_valid, f"Audit chain corrupted: {result.errors}")

    def test_totp_session_logout(self):
        """Verify user can log out after TOTP authentication, invalidating the session."""
        phone = "+15550192831"
        user = self.db.query(User).filter(User.phone_number == phone).first()
        code = pyotp.TOTP(user.totp_secret).now()

        login_res = self.client.post("/auth/totp/verify", json={"phone_number": phone, "totp_code": code})
        token = login_res.json()["session_token"]

        # Authenticated /auth/me call succeeds
        me_res = self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_res.status_code, 200)

        # Logout
        logout_res = self.client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(logout_res.status_code, 200)

        # Subsequent /auth/me call fails (401)
        me_res_after = self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_res_after.status_code, 401)
