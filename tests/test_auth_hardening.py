"""
Authentication hardening: secure defaults and the bypasses that existed around the demo features.

Every test uses throw-away in-memory databases and random values generated at run time; no credential is
hardcoded here or in the application.
"""
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pyotp
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.audit.ledger import AuditLedger
from app.main import app
from database.database import Base, get_db
from engine.auth.config import AuthConfig, DEMO_PERSONAS
from engine.auth.dependencies import get_auth_service
from engine.auth.models import AuthBase, OTPRecord, User
from engine.auth.providers.local import LocalDemoOTPProvider
from engine.auth.service import AuthService
from tests.test_client import LocalTestClient

DOCTOR = next(p["phone_number"] for p in DEMO_PERSONAS if p["role"] == "Doctor")
NURSE = next(p["phone_number"] for p in DEMO_PERSONAS if p["role"] == "Nurse")


def _random_otp() -> str:
    while True:
        code = f"{secrets.randbelow(900000) + 100000}"
        if code not in AuthConfig._WEAK_OTPS:
            return code


class AuthHardeningBase(unittest.TestCase):
    demo_mode = False

    def setUp(self):
        self._orig_otp = AuthConfig.DEMO_DEFAULT_OTP
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        AuthBase.metadata.create_all(bind=self.engine)
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = AuditLedger(db_path=os.path.join(self.tmp.name, "audit.db"))
        self.provider = LocalDemoOTPProvider(demo_mode=self.demo_mode)
        self.service = AuthService(audit_ledger=self.ledger, sms_provider=self.provider, demo_mode=self.demo_mode)
        self.service.seed_demo_users(self.db)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_auth_service] = lambda: self.service
        self.client = LocalTestClient(app)

    def tearDown(self):
        AuthConfig.DEMO_DEFAULT_OTP = self._orig_otp
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def user(self, phone):
        return self.db.query(User).filter(User.phone_number == phone).one()


class TestSecureDefaults(unittest.TestCase):
    def test_no_totp_secret_is_hardcoded_in_personas(self):
        self.assertEqual(len(DEMO_PERSONAS), 4)
        for persona in DEMO_PERSONAS:
            self.assertNotIn("totp_secret", persona)

    def test_defaults_in_a_clean_environment(self):
        # keep the DB/audit redirection from tests/conftest.py (so nothing opens ./aegis_rx.db); drop only the auth switches
        env = {k: v for k, v in os.environ.items()
               if k not in ("AEGIS_DEMO_MODE", "AEGIS_DEMO_DEFAULT_OTP", "AEGIS_COOKIE_SECURE")}
        out = subprocess.run(
            [sys.executable, "-c",
             "from engine.auth.config import AuthConfig as A; print(A.DEMO_MODE, A.DEMO_DEFAULT_OTP, A.COOKIE_SECURE)"],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], env=env, check=True,
        ).stdout.split()
        self.assertEqual(out, ["False", "None", "True"])

    def test_weak_or_malformed_demo_otp_is_ignored(self):
        original = AuthConfig.DEMO_DEFAULT_OTP
        try:
            for weak in ("123456", "000000", "12345", "abcdef", "1234567", "", None):
                AuthConfig.DEMO_DEFAULT_OTP = weak
                self.assertIsNone(AuthConfig.demo_otp(True), weak)
            AuthConfig.DEMO_DEFAULT_OTP = _random_otp()
            self.assertIsNotNone(AuthConfig.demo_otp(True))
            self.assertIsNone(AuthConfig.demo_otp(False), "demo OTP must never apply outside demo mode")
        finally:
            AuthConfig.DEMO_DEFAULT_OTP = original


class TestProductionMode(AuthHardeningBase):
    demo_mode = False

    def test_client_cannot_switch_demo_mode_on_to_get_the_otp(self):
        res = self.client.post("/auth/otp/request", json={"phone_number": DOCTOR, "demo_mode": True})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["is_demo_mode"])
        self.assertIsNone(data["preview_otp"])

    def test_configured_demo_otp_is_not_accepted(self):
        AuthConfig.DEMO_DEFAULT_OTP = _random_otp()
        self.client.post("/auth/otp/request", json={"phone_number": DOCTOR})
        # even a record flagged as demo must not make the fixed code valid
        for rec in self.db.query(OTPRecord).all():
            rec.is_demo = True
        self.db.commit()
        res = self.client.post("/auth/otp/verify", json={"phone_number": DOCTOR, "otp_code": AuthConfig.DEMO_DEFAULT_OTP})
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/auth/otp/verify", json={"phone_number": "+15559990000", "otp_code": AuthConfig.DEMO_DEFAULT_OTP})
        self.assertEqual(res.status_code, 400)

    def test_demo_totp_preview_endpoint_is_disabled(self):
        res = self.client.get(f"/auth/totp/demo-code/{DOCTOR}")
        self.assertEqual(res.status_code, 400)
        self.assertNotIn("code", res.json())

    def test_enrolled_account_cannot_be_taken_over_through_enrolment(self):
        before = self.user(DOCTOR).totp_secret
        res = self.client.post("/auth/totp/enroll/setup", json={"phone_number": DOCTOR, "role_hint": "Administrator"})
        self.assertEqual(res.status_code, 400)
        self.assertNotIn(before, res.text)
        user = self.user(DOCTOR)
        self.assertEqual(user.totp_secret, before)
        self.assertEqual(user.role, "Doctor")

    def test_login_cannot_escalate_role(self):
        user = self.user(NURSE)
        code = pyotp.TOTP(user.totp_secret).now()
        res = self.client.post("/auth/totp/verify", json={"phone_number": NURSE, "totp_code": code, "role": "Administrator"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["user"]["role"] if "user" in res.json() else self.user(NURSE).role, "Nurse")
        self.assertEqual(self.user(NURSE).role, "Nurse")

    def test_session_role_switch_is_refused(self):
        user = self.user(NURSE)
        code = pyotp.TOTP(user.totp_secret).now()
        token = self.client.post("/auth/totp/verify", json={"phone_number": NURSE, "totp_code": code}).json()["session_token"]
        with self.assertRaises(ValueError):
            self.service.switch_session_role(self.db, token, "Administrator")
        self.assertEqual(self.user(NURSE).role, "Nurse")

    def test_new_account_cannot_self_assign_administrator(self):
        with self.assertRaises(ValueError):
            self.service.setup_totp_enrollment(self.db, "+15557770001", role_hint="Administrator")

    def test_seeded_secrets_are_random_and_distinct(self):
        secrets_seen = {u.totp_secret for u in self.db.query(User).all()}
        self.assertEqual(len(secrets_seen), 4)
        other = AuthService(audit_ledger=self.ledger, sms_provider=self.provider, demo_mode=False)
        engine2 = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=engine2)
        AuthBase.metadata.create_all(bind=engine2)
        db2 = sessionmaker(bind=engine2)()
        other.seed_demo_users(db2)
        secrets_other = {u.totp_secret for u in db2.query(User).all()}
        self.assertFalse(secrets_seen & secrets_other, "two fresh databases must not share any TOTP secret")
        db2.close()


class TestDemoMode(AuthHardeningBase):
    demo_mode = True

    def test_fixed_demo_otp_only_for_seeded_demo_accounts(self):
        AuthConfig.DEMO_DEFAULT_OTP = _random_otp()
        res = self.client.post("/auth/otp/verify", json={"phone_number": "+15559990000", "otp_code": AuthConfig.DEMO_DEFAULT_OTP})
        self.assertEqual(res.status_code, 400, "the fixed demo OTP must not create or open arbitrary accounts")
        self.client.post("/auth/otp/request", json={"phone_number": DOCTOR})
        res = self.client.post("/auth/otp/verify", json={"phone_number": DOCTOR, "otp_code": AuthConfig.DEMO_DEFAULT_OTP, "role": "Doctor"})
        self.assertEqual(res.status_code, 200)

    def test_without_a_configured_demo_otp_a_random_code_is_issued(self):
        AuthConfig.DEMO_DEFAULT_OTP = None
        data = self.client.post("/auth/otp/request", json={"phone_number": DOCTOR}).json()
        self.assertTrue(data["is_demo_mode"])
        self.assertRegex(data["preview_otp"], r"^\d{6}$")

    def test_demo_totp_preview_works_in_demo_mode_for_demo_accounts_only(self):
        ok = self.client.get(f"/auth/totp/demo-code/{DOCTOR}")
        self.assertEqual(ok.status_code, 200)
        self.assertRegex(ok.json()["code"], r"^\d{6}$")
        self.assertEqual(self.client.get("/auth/totp/demo-code/+15559990000").status_code, 400)

    def test_enrolled_account_still_cannot_be_taken_over_in_demo_mode(self):
        res = self.client.post("/auth/totp/enroll/setup", json={"phone_number": DOCTOR})
        self.assertEqual(res.status_code, 400)


class TestNoSecretsInSource(unittest.TestCase):
    def test_no_base32_secret_literals_in_application_code(self):
        import re
        root = Path(__file__).resolve().parents[1]
        pattern = re.compile(r"[\"'][A-Z2-7]{32}[\"']")
        hits = []
        for folder in ("engine", "app", "agents", "database", "models", "ml", "scripts"):
            for path in (root / folder).rglob("*.py"):
                if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                    hits.append(str(path.relative_to(root)))
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
