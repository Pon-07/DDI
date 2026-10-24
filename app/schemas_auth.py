from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class DemoPersona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Unique persona key (e.g. doctor, nurse, pharmacist, administrator)")
    name: str = Field(..., description="Full display name of the demo persona")
    username: Optional[str] = Field(None, description="Username of persona")
    phone_number: str = Field(..., description="Normalized E.164 demo phone number")
    role: str = Field(..., description="Clinical or administrative role")
    demo_mode_allowed: bool = Field(default=True, description="Whether persona is permitted for demo login")


class DemoPersonaListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    demo_mode: bool = Field(..., description="Current server-side demo mode status")
    personas: List[DemoPersona] = Field(default_factory=list, description="Configured demo personas")


class DemoLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_identifier: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Configured persona identifier or normalized phone number",
    )


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    username: str
    full_name: str
    phone_number: Optional[str] = None
    role: str
    is_active: bool
    demo_mode_allowed: bool


class AuthSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str
    user: UserResponse
    role: str
    expires_at: datetime
    message: str = "Login successful"


class PasswordLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str


class OTPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_number: str


class OTPVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_number: str
    otp_code: str


class RoleSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_role: str


class TOTPSetupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: str
    provisioning_uri: Optional[str] = None


class TOTPVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    totp_code: str


class LogoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "logged_out"
    message: str = "Session successfully terminated"
