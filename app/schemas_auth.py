from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class OTPRequestSchema(BaseModel):
    phone_number: str = Field(..., min_length=7, max_length=20, description="Mobile number (E.164 or 10-digit)")
    role_hint: Optional[str] = Field(None, description="Optional role pre-selection for demo personas")
    demo_mode: Optional[bool] = Field(None, description="Force demo mode preview")


class OTPResponseSchema(BaseModel):
    success: bool
    message: str
    phone_number: str
    phone_number_masked: str
    provider: str
    expires_in_seconds: int
    resend_cooldown_seconds: int
    is_demo_mode: bool
    preview_otp: Optional[str] = None
    user_found: bool
    user_role: Optional[str] = None
    user_name: Optional[str] = None


class OTPVerifySchema(BaseModel):
    phone_number: str = Field(..., min_length=7, max_length=20, description="Mobile number")
    otp_code: str = Field(..., min_length=4, max_length=10, description="6-digit verification code")
    role: Optional[str] = Field(None, description="Clinical role (Doctor, Nurse, Clinical Pharmacist, Administrator)")
    full_name: Optional[str] = Field(None, max_length=255, description="Full clinician name if registering")


class UserResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phone_number: str
    phone_number_masked: Optional[str] = None
    full_name: str
    role: str
    department: Optional[str] = None
    license_number: Optional[str] = None
    email: Optional[str] = None
    is_active: bool = True
    totp_enabled: bool = False
    created_at: Optional[str] = None
    last_login_at: Optional[str] = None


class AuthSessionResponse(BaseModel):
    success: bool
    session_token: str
    user: UserResponseSchema
    role: str
    dashboard_url: str
    expires_at: str
    auth_method: str = "sms_otp"  # 'sms_otp' or 'totp'


class DemoPersonaResponse(BaseModel):
    phone_number: str
    alt_phone: Optional[str] = None
    full_name: str
    role: str
    department: Optional[str] = None
    license_number: Optional[str] = None
    email: Optional[str] = None
    avatar: str
    description: str
    totp_enabled: bool = True


class ProviderStatusResponse(BaseModel):
    providers: List[Dict[str, Any]]
    active_provider: str
    demo_mode: bool
    offline_capable: bool


class ProviderSwitchRequest(BaseModel):
    provider_name: str = Field(..., description="'local', 'msg91', or 'twilio'")


# --- TOTP (RFC 6238 Offline Authenticator) Schemas ---

class TOTPEnrollSetupRequest(BaseModel):
    phone_number: str = Field(..., min_length=7, max_length=20, description="User mobile number")
    role_hint: Optional[str] = Field(None, description="Target role (Doctor, Nurse, Clinical Pharmacist, Administrator)")
    full_name: Optional[str] = Field(None, max_length=255, description="Clinician name")


class TOTPEnrollSetupResponse(BaseModel):
    success: bool
    phone_number: str
    secret_base32: str
    secret_formatted: str
    otpauth_uri: str
    qr_code_data_uri: str
    account_name: str
    issuer: str = "MICROMEDX"
    digits: int = 6
    interval_seconds: int = 30
    message: str = "Scan QR code or enter base32 secret in your Authenticator app."


class TOTPEnrollVerifyRequest(BaseModel):
    phone_number: str = Field(..., min_length=7, max_length=20, description="User mobile number")
    totp_code: str = Field(..., min_length=6, max_length=8, description="6-digit authenticator code")
    role: Optional[str] = Field(None, description="Optional role update")


class TOTPLoginRequest(BaseModel):
    phone_number: str = Field(..., min_length=7, max_length=20, description="Mobile number / User ID")
    totp_code: str = Field(..., min_length=6, max_length=8, description="6-digit authenticator code")
    role: Optional[str] = Field(None, description="Role selection for unseeded/new clinician")


class TOTPStatusResponse(BaseModel):
    phone_number: str
    totp_enabled: bool
    user_found: bool
    user_name: Optional[str] = None
    user_role: Optional[str] = None
