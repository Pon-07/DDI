import os
from typing import Optional


class AuthConfig:
    """Configuration for MICROMEDX Mobile OTP and Offline TOTP Authentication."""

    # Explicit Demo Mode flag (defaults to True for offline hackathon/demo readiness)
    DEMO_MODE: bool = os.getenv("AEGIS_DEMO_MODE", "true").lower() in ("true", "1", "yes")

    # Hardcoded demo OTP accepted only when DEMO_MODE is True for SMS demo provider
    DEMO_DEFAULT_OTP: str = os.getenv("AEGIS_DEMO_DEFAULT_OTP", "123456")

    # Default SMS Provider: 'local', 'msg91', or 'twilio'
    SMS_PROVIDER: str = os.getenv("AEGIS_SMS_PROVIDER", "local").lower()

    # OTP expiry in seconds (default: 300s / 5 minutes)
    OTP_EXPIRY_SECONDS: int = int(os.getenv("AEGIS_OTP_EXPIRY_SECONDS", "300"))

    # Maximum failed OTP verification attempts before invalidation
    OTP_MAX_ATTEMPTS: int = int(os.getenv("AEGIS_OTP_MAX_ATTEMPTS", "5"))

    # Resend cooldown in seconds (default: 30s)
    OTP_RESEND_COOLDOWN_SECONDS: int = int(os.getenv("AEGIS_OTP_RESEND_COOLDOWN_SECONDS", "30"))

    # Session token expiration in hours (default: 24h)
    SESSION_EXPIRY_HOURS: int = int(os.getenv("AEGIS_SESSION_EXPIRY_HOURS", "24"))

    # TOTP Settings (RFC 6238 standard: 6 digits, 30-second time-step, SHA1 algorithm)
    TOTP_ISSUER: str = "MICROMEDX"
    TOTP_DIGITS: int = 6
    TOTP_INTERVAL_SECONDS: int = 30
    TOTP_ALLOWED_DRIFT_WINDOWS: int = 1  # Allows current ± 1 window (30s) for clock drift

    # MSG91 Configuration
    MSG91_AUTH_KEY: Optional[str] = os.getenv("MSG91_AUTH_KEY")
    MSG91_TEMPLATE_ID: Optional[str] = os.getenv("MSG91_TEMPLATE_ID")
    MSG91_SENDER_ID: Optional[str] = os.getenv("MSG91_SENDER_ID", "MICROMDX")

    # Twilio Configuration
    TWILIO_ACCOUNT_SID: Optional[str] = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN: Optional[str] = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_FROM_PHONE: Optional[str] = os.getenv("TWILIO_FROM_PHONE")


# Supported user roles
SUPPORTED_ROLES = [
    "Doctor",
    "Nurse",
    "Clinical Pharmacist",
    "Administrator",
]

# Pre-configured demo personas with dedicated unique base32 TOTP secrets
DEMO_PERSONAS = [
    {
        "phone_number": "+15550192831",
        "alt_phone": "9876543210",
        "full_name": "Dr. Sarah Lin, MD",
        "role": "Doctor",
        "department": "Cardiology & Intensive Care",
        "license_number": "MD-884910",
        "email": "s.lin@micromedx-health.org",
        "avatar": "👨‍⚕️",
        "description": "Attending Physician with clinical prescribing, finding review, and order co-sign authority.",
        # Standard RFC 6238 Base32 secret unique to Doctor persona
        "totp_secret": "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
    },
    {
        "phone_number": "+15550192832",
        "alt_phone": "9876543211",
        "full_name": "Elena Rostova, RN",
        "role": "Nurse",
        "department": "Cardiothoracic Step-Down Unit",
        "license_number": "RN-554210",
        "email": "e.rostova@micromedx-health.org",
        "avatar": "👩‍⚕️",
        "description": "Lead Inpatient Nurse managing Medication Administration Record (MAR) and rapid vitals/labs.",
        # Standard RFC 6238 Base32 secret unique to Nurse persona
        "totp_secret": "KRSXG5CTMVRXEZLUKRSXG5CTMVRXEZLU",
    },
    {
        "phone_number": "+15550192833",
        "alt_phone": "9876543212",
        "full_name": "Marcus Vance, PharmD, BCPS",
        "role": "Clinical Pharmacist",
        "department": "Clinical Pharmacology & Toxicology",
        "license_number": "RPH-992144",
        "email": "m.vance@micromedx-health.org",
        "avatar": "💊",
        "description": "Board-Certified Pharmacist conducting DDI analysis, renal dosing, and therapeutic substitutions.",
        # Standard RFC 6238 Base32 secret unique to Pharmacist persona
        "totp_secret": "MFRGGZDFMYXW65ZTMFRGGZDFMYXW65ZT",
    },
    {
        "phone_number": "+15550192834",
        "alt_phone": "9876543213",
        "full_name": "Arthur Pendelton, MS, CPHIMS",
        "role": "Administrator",
        "department": "Health Informatics & Clinical Governance",
        "license_number": "ADM-001042",
        "email": "admin@micromedx-health.org",
        "avatar": "🛡️",
        "description": "System & Security Administrator overseeing offline engine health, audit ledger, and user roles.",
        # Standard RFC 6238 Base32 secret unique to Admin persona
        "totp_secret": "NBSWY3DPEHPK3PXPNBSWY3DPEHPK3PXP",
    },
]
