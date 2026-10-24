import os
import re
from typing import Any, Dict, Optional


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
    """Authentication configuration and synthetic persona registry."""

    COOKIE_NAME: str = "aegis_session"
    SESSION_DURATION_SECONDS: int = 12 * 3600  # 12 hours

    @classmethod
    def is_demo_mode(cls) -> bool:
        """Evaluate if server-side demo mode is enabled via environment variable."""
        val = os.getenv("AEGIS_DEMO_MODE", "false").strip().lower()
        return val in {"true", "1", "yes", "on"}

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

        # Phone number check
        norm_phone = normalize_phone(identifier)
        for persona in cls.DEMO_PERSONAS.values():
            if normalize_phone(persona["phone_number"]) == norm_phone:
                return persona

        return None
