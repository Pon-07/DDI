"""Authentication settings and synthetic demo profiles.

Secrets are read from the process environment; this module contains no credentials.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional


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


def normalize_phone(phone: str) -> str:
    """Normalize phone number to digits or standard E.164 representation."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone.strip())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}" if digits else ""


class AuthConfig:
    """Environment-backed auth settings with production-safe defaults."""

    COOKIE_NAME: str = "aegis_session"
    SESSION_DURATION_SECONDS: int = 12 * 3600  # 12 hours

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
    def is_demo_mode(cls) -> bool:
        """Evaluate if server-side demo mode is enabled via environment variable."""
        val = os.getenv("AEGIS_DEMO_MODE", "false").strip().lower()
        return val in {"true", "1", "yes", "on"}

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

    DEMO_PERSONAS: Dict[str, Dict[str, Any]] = {
        "doctor": {
            "id": "doctor",
            "name": "Demo Doctor",
            "username": "demo_doctor",
            "phone_number": "+15555550101",
            "role": "doctor",
            "demo_mode_allowed": True,
        },
        "nurse": {
            "id": "nurse",
            "name": "Demo Nurse",
            "username": "demo_nurse",
            "phone_number": "+15555550102",
            "role": "nurse",
            "demo_mode_allowed": True,
        },
        "pharmacist": {
            "id": "pharmacist",
            "name": "Demo Pharmacist",
            "username": "demo_pharmacist",
            "phone_number": "+15555550103",
            "role": "pharmacist",
            "demo_mode_allowed": True,
        },
        "administrator": {
            "id": "administrator",
            "name": "Demo Administrator",
            "username": "demo_admin",
            "phone_number": "+15555550104",
            "role": "administrator",
            "demo_mode_allowed": True,
        },
    }

    @classmethod
    def resolve_persona(cls, identifier: str) -> Optional[Dict[str, Any]]:
        """
        Deterministically resolve persona by key (e.g. 'doctor', 'admin') or phone number.
        Returns None if identifier does not match any configured persona.
        """
        if not identifier or not isinstance(identifier, str):
            return None

        clean_id = identifier.strip().lower()
        # Direct key alias check
        if clean_id in cls.DEMO_PERSONAS:
            return cls.DEMO_PERSONAS[clean_id]
        if clean_id in {"admin", "administrator"}:
            return cls.DEMO_PERSONAS["administrator"]

        # Phone number check against DEMO_PERSONAS
        norm_phone = normalize_phone(identifier)
        for persona in cls.DEMO_PERSONAS.values():
            if normalize_phone(persona["phone_number"]) == norm_phone:
                return persona

        # Also support DEMO_PERSONAS list from origin/main
        for persona in DEMO_PERSONAS:
            if normalize_phone(persona["phone_number"]) == norm_phone:
                role_lower = persona["role"].lower().replace("clinical ", "")
                return {
                    "id": role_lower,
                    "name": persona["full_name"],
                    "username": f"demo_{role_lower}",
                    "phone_number": persona["phone_number"],
                    "role": persona["role"],
                    "demo_mode_allowed": True,
                }

        return None


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
