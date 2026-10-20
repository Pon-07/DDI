"""Safe configuration and startup regression tests; application DB is redirected by conftest."""

import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_missing_auth_environment_uses_secure_defaults():
    env = {key: value for key, value in os.environ.items() if not key.startswith("AEGIS_")}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from engine.auth.config import AuthConfig as C; "
            "assert C.DEMO_MODE is False; assert C.DEMO_DEFAULT_OTP is None; "
            "assert C.COOKIE_SECURE is True; assert C.MSG91_AUTH_KEY is None; "
            "assert C.TWILIO_AUTH_TOKEN is None; print('secure defaults ok')",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "secure defaults ok"


def test_demo_personas_contain_no_credentials():
    from engine.auth.config import DEMO_PERSONAS, SUPPORTED_ROLES

    assert [persona["role"] for persona in DEMO_PERSONAS] == SUPPORTED_ROLES
    assert all("totp_secret" not in persona and "token" not in persona for persona in DEMO_PERSONAS)


def test_normal_application_startup_health_endpoint():
    from app.main import app
    from tests.test_client import LocalTestClient

    response = LocalTestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
