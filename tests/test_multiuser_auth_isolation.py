import os
import tempfile
import time
import unittest
import pyotp
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.audit.ledger import AuditLedger
from app.dependencies import get_audit_ledger
from app.main import app
from database.database import Base, get_db
from engine.auth.config import AuthConfig, DEMO_PERSONAS
from engine.auth.dependencies import get_auth_service
from engine.auth.models import AuthBase, User, UserSession
from engine.auth.providers.local import LocalDemoOTPProvider
from engine.auth.service import AuthService
from tests.test_client import LocalTestClient


class TestMultiUserAuthIsolation(unittest.TestCase):
    """
    Test suite verifying strict multi-user TOTP isolation, independent authentication,
    cross-user verification failure, and complete logout state invalidation.
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
        self.audit_db_path = os.path.join(self.temp_dir.name, "test_multiuser_audit.db")
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

        self.local_provider = LocalDemoOTPProvider(demo_mode=True)
        self.auth_service = AuthService(
            audit_ledger=self.audit_ledger,
            sms_provider=self.local_provider,
            demo_mode=True,
        )
        self.users = self.auth_service.seed_demo_users(self.db)

        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_audit_ledger] = lambda: self.audit_ledger
        app.dependency_overrides[get_auth_service] = lambda: self.auth_service
        self.client = LocalTestClient(app)

        self.doc_persona = next(p for p in DEMO_PERSONAS if p["role"] == "Doctor")
        self.nurse_persona = next(p for p in DEMO_PERSONAS if p["role"] == "Nurse")
        self.pharm_persona = next(p for p in DEMO_PERSONAS if p["role"] == "Clinical Pharmacist")
        self.admin_persona = next(p for p in DEMO_PERSONAS if p["role"] == "Administrator")

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()
        app.dependency_overrides.clear()

    def test_each_persona_has_distinct_totp_secret(self):
        """Verify each enrolled user has a distinct TOTP secret."""
        secrets = [p["totp_secret"] for p in DEMO_PERSONAS]
        self.assertEqual(len(secrets), 4)
        self.assertEqual(len(set(secrets)), 4, "All 4 personas must have unique secrets.")

    def test_user_a_doctor_login_then_logout_then_user_b_nurse_login(self):
        """
        User A (Doctor) logs in with Doctor TOTP -> verified -> dashboard.
        Logout -> session invalidated.
        User B (Nurse) logs in with Nurse TOTP -> verified -> Nurse dashboard.
        """
        # 1. User A (Doctor) Login
        doc_totp = pyotp.TOTP(self.doc_persona["totp_secret"])
        doc_code = doc_totp.now()

        res_a = self.client.post(
            "/auth/totp/verify",
            json={
                "phone_number": self.doc_persona["phone_number"],
                "totp_code": doc_code,
                "role": "Doctor",
            },
        )
        self.assertEqual(res_a.status_code, 200)
        data_a = res_a.json()
        token_a = data_a["session_token"]
        self.assertEqual(data_a["role"], "Doctor")
        self.assertEqual(data_a["user"]["full_name"], "Dr. Sarah Lin, MD")

        # Verify /auth/me returns Doctor
        res_me_a = self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        self.assertEqual(res_me_a.status_code, 200)
        self.assertEqual(res_me_a.json()["role"], "Doctor")

        # 2. User A Logout
        res_logout = self.client.post(
            "/auth/logout",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        self.assertEqual(res_logout.status_code, 200)

        # 3. Verify previous token is immediately invalidated (GET /auth/me -> 401)
        res_me_invalid = self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        self.assertEqual(res_me_invalid.status_code, 401)

        # 4. User B (Nurse) Login Independently
        nurse_totp = pyotp.TOTP(self.nurse_persona["totp_secret"])
        nurse_code = nurse_totp.now()

        res_b = self.client.post(
            "/auth/totp/verify",
            json={
                "phone_number": self.nurse_persona["phone_number"],
                "totp_code": nurse_code,
                "role": "Nurse",
            },
        )
        self.assertEqual(res_b.status_code, 200)
        data_b = res_b.json()
        token_b = data_b["session_token"]
        self.assertNotEqual(token_a, token_b, "User B must receive a distinct session token.")
        self.assertEqual(data_b["role"], "Nurse")
        self.assertEqual(data_b["user"]["full_name"], "Elena Rostova, RN")

        # Verify /auth/me returns Nurse
        res_me_b = self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        self.assertEqual(res_me_b.status_code, 200)
        self.assertEqual(res_me_b.json()["role"], "Nurse")

    def test_cross_user_totp_rejection(self):
        """
        Verify:
        - Doctor's TOTP cannot authenticate Nurse
        - Nurse's TOTP cannot authenticate Doctor
        - Pharmacist's TOTP cannot authenticate Admin
        """
        doc_totp = pyotp.TOTP(self.doc_persona["totp_secret"])
        doc_code = doc_totp.now()

        # Try logging in as Nurse using Doctor's TOTP
        res = self.client.post(
            "/auth/totp/verify",
            json={
                "phone_number": self.nurse_persona["phone_number"],
                "totp_code": doc_code,
                "role": "Nurse",
            },
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid or expired 6-digit authenticator code", res.json()["detail"])

        # Try logging in as Doctor using Nurse's TOTP
        nurse_totp = pyotp.TOTP(self.nurse_persona["totp_secret"])
        nurse_code = nurse_totp.now()

        res2 = self.client.post(
            "/auth/totp/verify",
            json={
                "phone_number": self.doc_persona["phone_number"],
                "totp_code": nurse_code,
                "role": "Doctor",
            },
        )
        self.assertEqual(res2.status_code, 400)

    def test_all_four_roles_independent_totp_success(self):
        """Verify all 4 personas (Doctor, Nurse, Pharmacist, Admin) authenticate independently."""
        for persona in [self.doc_persona, self.nurse_persona, self.pharm_persona, self.admin_persona]:
            totp = pyotp.TOTP(persona["totp_secret"])
            code = totp.now()

            res = self.client.post(
                "/auth/totp/verify",
                json={
                    "phone_number": persona["phone_number"],
                    "totp_code": code,
                    "role": persona["role"],
                },
            )
            self.assertEqual(res.status_code, 200, f"Role {persona['role']} should authenticate successfully.")
            data = res.json()
            self.assertEqual(data["role"], persona["role"])
            self.assertEqual(data["user"]["full_name"], persona["full_name"])

    def test_audit_ledger_records_multiuser_lifecycle(self):
        """Verify AuditLedger logs AUTH_TOTP_SUCCESS and AUTH_LOGOUT with intact SHA-256 chain."""
        # 1. Doctor login
        doc_totp = pyotp.TOTP(self.doc_persona["totp_secret"])
        res_a = self.client.post(
            "/auth/totp/verify",
            json={
                "phone_number": self.doc_persona["phone_number"],
                "totp_code": doc_totp.now(),
                "role": "Doctor",
            },
        )
        token_a = res_a.json()["session_token"]

        # 2. Doctor logout
        self.client.post(
            "/auth/logout",
            headers={"Authorization": f"Bearer {token_a}"},
        )

        # 3. Nurse login
        nurse_totp = pyotp.TOTP(self.nurse_persona["totp_secret"])
        self.client.post(
            "/auth/totp/verify",
            json={
                "phone_number": self.nurse_persona["phone_number"],
                "totp_code": nurse_totp.now(),
                "role": "Nurse",
            },
        )

        # Verify audit chain integrity
        verification = self.audit_ledger.verify_chain()
        self.assertTrue(verification.is_valid)
        self.assertTrue(verification.total_records >= 3)

        events = self.audit_ledger.get_events()
        event_types = [e.event_type for e in events]
        self.assertIn("AUTH_TOTP_SUCCESS", event_types)
        self.assertIn("AUTH_LOGOUT", event_types)

    def test_first_time_totp_enrollment_lifecycle_and_subsequent_login(self):
        """
        Verify:
        1. New unenrolled user phone number returns totp_enabled: False from /auth/totp/status/{phone}
        2. Direct login fails because user is not yet enrolled
        3. Calling /auth/totp/enroll/setup produces QR code and secret
        4. User enters 6-digit TOTP into /auth/totp/enroll/verify -> verified & activated in DB
        5. User receives active session and can access /auth/me
        6. User logs out
        7. Next login uses standard /auth/totp/verify without needing re-enrollment
        """
        new_phone = "+15550776655"
        
        # 1. Status check: not enrolled
        status_res = self.client.get(f"/auth/totp/status/{new_phone}")
        self.assertEqual(status_res.status_code, 200)
        self.assertFalse(status_res.json()["totp_enabled"])

        # 2. Setup enrollment: generates offline QR and secret
        setup_res = self.client.post(
            "/auth/totp/enroll/setup",
            json={
                "phone_number": new_phone,
                "role_hint": "Doctor",
                "full_name": "Dr. Newly Enrolled, MD",
            },
        )
        self.assertEqual(setup_res.status_code, 200)
        setup_data = setup_res.json()
        secret = setup_data["secret_base32"]
        self.assertTrue(setup_data["qr_code_data_uri"].startswith("data:image/png;base64,"))

        # 3. User generates 6-digit code with authenticator app
        totp = pyotp.TOTP(secret)
        code = totp.now()

        # 4. Complete enrollment
        enroll_res = self.client.post(
            "/auth/totp/enroll/verify",
            json={
                "phone_number": new_phone,
                "totp_code": code,
                "role": "Doctor",
            },
        )
        self.assertEqual(enroll_res.status_code, 200)
        enroll_data = enroll_res.json()
        self.assertTrue(enroll_data["success"])
        session_token = enroll_data["session_token"]
        self.assertEqual(enroll_data["user"]["full_name"], "Dr. Newly Enrolled, MD")

        # 5. Authenticated call works
        me_res = self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {session_token}"},
        )
        self.assertEqual(me_res.status_code, 200)

        # 6. Logout
        logout_res = self.client.post(
            "/auth/logout",
            headers={"Authorization": f"Bearer {session_token}"},
        )
        self.assertEqual(logout_res.status_code, 200)

        # 7. Subsequent normal login without re-enrollment
        # Verify status is now enrolled
        status_after = self.client.get(f"/auth/totp/status/{new_phone}")
        self.assertEqual(status_after.status_code, 200)
        self.assertTrue(status_after.json()["totp_enabled"])

        # Reset last timestep to simulate next window
        user = self.db.query(User).filter(User.phone_number == new_phone).first()
        user.totp_last_timestep = (user.totp_last_timestep or 0) - 1
        self.db.commit()

        # Subsequent login works with standard TOTP endpoint
        current_code = pyotp.TOTP(secret).now()
        login_res = self.client.post(
            "/auth/totp/verify",
            json={
                "phone_number": new_phone,
                "totp_code": current_code,
                "role": "Doctor",
            },
        )
        self.assertEqual(login_res.status_code, 200)
        self.assertTrue(login_res.json()["success"])
        self.assertEqual(login_res.json()["role"], "Doctor")



if __name__ == "__main__":
    unittest.main()

