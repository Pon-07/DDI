"""Authentication settings and synthetic demo profiles.

Secrets are read from the process environment; this module contains no credentials.
"""

from __future__ import annotations

import os
import re


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _env_int(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is outside its allowed range")
    return value


class AuthConfig:
    """Environment-backed auth settings with production-safe defaults."""

    DEMO_MODE = _env_bool("AEGIS_DEMO_MODE", False)
    DEMO_DEFAULT_OTP = os.getenv("AEGIS_DEMO_DEFAULT_OTP") or None
    COOKIE_SECURE = _env_bool("AEGIS_COOKIE_SECURE", True)
    SMS_PROVIDER = os.getenv("AEGIS_SMS_PROVIDER", "local").strip().lower() or "local"

    OTP_EXPIRY_SECONDS = _env_int("AEGIS_OTP_EXPIRY_SECONDS", 300, 1)
    OTP_MAX_ATTEMPTS = _env_int("AEGIS_OTP_MAX_ATTEMPTS", 5, 1)
    OTP_RESEND_COOLDOWN_SECONDS = _env_int("AEGIS_OTP_RESEND_COOLDOWN_SECONDS", 30, 0)
    SESSION_EXPIRY_HOURS = _env_int("AEGIS_SESSION_EXPIRY_HOURS", 24, 1)

    TOTP_ISSUER = os.getenv("AEGIS_TOTP_ISSUER", "MICROMEDX").strip() or "MICROMEDX"
    TOTP_DIGITS = _env_int("AEGIS_TOTP_DIGITS", 6, 6, 8)
    TOTP_INTERVAL_SECONDS = _env_int("AEGIS_TOTP_INTERVAL_SECONDS", 30, 1)
    TOTP_ALLOWED_DRIFT_WINDOWS = _env_int("AEGIS_TOTP_ALLOWED_DRIFT_WINDOWS", 1, 0, 10)

    # Provider credentials are optional until that provider is selected.
    MSG91_AUTH_KEY = os.getenv("MSG91_AUTH_KEY")
    MSG91_TEMPLATE_ID = os.getenv("MSG91_TEMPLATE_ID")
    MSG91_SENDER_ID = os.getenv("MSG91_SENDER_ID")
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_FROM_PHONE = os.getenv("TWILIO_FROM_PHONE")

    _WEAK_OTPS = frozenset(
        {"000000", "111111", "222222", "333333", "444444", "555555", "666666",
         "777777", "888888", "999999", "123456", "654321"}
    )

    @classmethod
    def demo_otp(cls, demo_mode: bool) -> str | None:
        """Return an explicitly configured demo OTP only in demo mode."""
        candidate = cls.DEMO_DEFAULT_OTP
        if not demo_mode or not isinstance(candidate, str):
            return None
        candidate = candidate.strip()
        if not re.fullmatch(r"\d{6}", candidate) or candidate in cls._WEAK_OTPS:
            return None
        return candidate


SUPPORTED_ROLES = ["Doctor", "Nurse", "Clinical Pharmacist", "Administrator"]

# Synthetic test/demo identities only. TOTP secrets are generated per database by AuthService;
# no secret or reusable token is stored in these profiles.
DEMO_PERSONAS = [
    {
        "phone_number": "+15550192831",
        "alt_phone": None,
        "full_name": "Dr. Sarah Lin, MD",
        "role": "Doctor",
        "department": "Clinical",
        "license_number": "LIC-MD-9041",
        "email": "sarah.lin@micromedx-health.org",
        "avatar": "",
        "description": "Attending Physician and Clinical Lead.",
    },
    {
        "phone_number": "+15550192832",
        "alt_phone": None,
        "full_name": "Elena Rostova, RN",
        "role": "Nurse",
        "department": "Clinical",
        "license_number": "LIC-RN-4402",
        "email": "elena.rostova@micromedx-health.org",
        "avatar": "",
        "description": "Lead Inpatient Medication Administration Nurse.",
    },
    {
        "phone_number": "+15550192833",
        "alt_phone": None,
        "full_name": "Marcus Vance, PharmD, BCPS",
        "role": "Clinical Pharmacist",
        "department": "Pharmacy",
        "license_number": "LIC-PH-8119",
        "email": "marcus.vance@micromedx-health.org",
        "avatar": "",
        "description": "Board Certified Clinical Pharmacist.",
    },
    {
        "phone_number": "+15550192834",
        "alt_phone": None,
        "full_name": "Arthur Pendelton, MS, CPHIMS",
        "role": "Administrator",
        "department": "Operations",
        "license_number": "LIC-ADM-001",
        "email": "arthur.pendelton@micromedx-health.org",
        "avatar": "",
        "description": "Health IT Security and Systems Administrator.",
    },
]
