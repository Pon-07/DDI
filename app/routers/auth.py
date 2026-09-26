from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.schemas_auth import (
    AuthSessionResponse,
    DemoPersonaResponse,
    OTPRequestSchema,
    OTPResponseSchema,
    OTPVerifySchema,
    ProviderStatusResponse,
    ProviderSwitchRequest,
    TOTPEnrollSetupRequest,
    TOTPEnrollSetupResponse,
    TOTPEnrollVerifyRequest,
    TOTPLoginRequest,
    TOTPStatusResponse,
    UserResponseSchema,
)
from database.database import get_db
from engine.auth.config import AuthConfig, DEMO_PERSONAS, SUPPORTED_ROLES
from engine.auth.dependencies import (
    get_auth_service,
    get_current_user,
    get_optional_user,
    get_session_token_from_request,
)
from engine.auth.models import User
from engine.auth.providers.factory import (
    get_sms_provider,
    list_available_providers,
    set_active_sms_provider,
)
from engine.auth.providers.local import LocalDemoOTPProvider
from engine.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication & Roles"])


@router.get("/demo-personas", response_model=List[DemoPersonaResponse], summary="Retrieve pre-configured demo personas")
def get_demo_personas():
    """Returns the 4 official demo personas for offline evaluation."""
    return DEMO_PERSONAS


@router.post("/otp/request", response_model=OTPResponseSchema, summary="Request 6-digit OTP for mobile authentication")
def request_otp(
    payload: OTPRequestSchema,
    request: Request,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    Dispatches a verification code to the mobile number.
    In DEMO_MODE, returns preview OTP for seamless offline evaluation.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    try:
        res = auth_service.request_otp(
            db=db,
            phone_number=payload.phone_number,
            role_hint=payload.role_hint,
            demo_mode=payload.demo_mode,
            client_ip=client_ip,
        )
        return res
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post("/otp/verify", response_model=AuthSessionResponse, summary="Verify OTP and obtain authenticated session")
def verify_otp(
    payload: OTPVerifySchema,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    Validates the 6-digit OTP, creates or retrieves the User record, establishes an active session,
    and returns the designated role-specific dashboard routing destination.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent", "Unknown")

    try:
        session_data = auth_service.verify_otp(
            db=db,
            phone_number=payload.phone_number,
            otp_code=payload.otp_code,
            role=payload.role,
            full_name=payload.full_name,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        # Set session cookie for seamless browser navigation
        response.set_cookie(
            key="aegis_session",
            value=session_data["session_token"],
            httponly=True,
            samesite="lax",
            max_age=AuthConfig.SESSION_EXPIRY_HOURS * 3600,
        )

        return session_data
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get("/me", response_model=UserResponseSchema, summary="Get current authenticated user profile")
def get_current_user_profile(
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Returns the authenticated clinician's profile and active role."""
    return UserResponseSchema(
        id=current_user.id,
        phone_number=current_user.phone_number,
        phone_number_masked=auth_service.mask_phone(current_user.phone_number),
        full_name=current_user.full_name,
        role=current_user.role,
        department=current_user.department,
        license_number=current_user.license_number,
        email=current_user.email,
        is_active=current_user.is_active,
        created_at=current_user.created_at.strftime("%Y-%m-%d %H:%M:%S") if current_user.created_at else None,
        last_login_at=current_user.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if current_user.last_login_at else None,
    )


@router.post("/logout", summary="Log out and invalidate current session")
def logout(
    response: Response,
    token: Optional[str] = Depends(get_session_token_from_request),
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Inactivates the current session token."""
    response.delete_cookie("aegis_session")
    if token:
        auth_service.logout(db, token)
    return {"success": True, "message": "Successfully logged out."}


@router.get("/users", summary="List all registered system users")
def list_users(
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Returns directory of users in the system."""
    return auth_service.list_users(db)


@router.get("/providers", response_model=ProviderStatusResponse, summary="Inspect SMS/OTP providers and active gateway")
def get_providers_status():
    """Inspect status of Local Offline, MSG91, and Twilio SMS providers."""
    providers = list_available_providers()
    active_prov = get_sms_provider()
    return ProviderStatusResponse(
        providers=providers,
        active_provider=active_prov.get_provider_name(),
        demo_mode=AuthConfig.DEMO_MODE,
        offline_capable=True,
    )


@router.post("/providers/switch", summary="Switch active SMS gateway provider")
def switch_provider(payload: ProviderSwitchRequest):
    """Dynamically switches between 'local', 'msg91', and 'twilio' providers."""
    valid = ["local", "local_demo", "offline", "msg91", "twilio"]
    target = payload.provider_name.lower().strip()
    if target not in valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider '{payload.provider_name}'. Must be one of: {valid}",
        )
    active = set_active_sms_provider(target)
    return {
        "success": True,
        "message": f"Active SMS provider switched to '{active.get_provider_name()}'",
        "provider_status": active.get_status(),
    }


@router.get("/recent-dispatches", summary="Get recent simulated SMS inbox messages (demo inspection)")
def get_recent_dispatches():
    """Retrieves the recent simulated SMS dispatch log for offline UI preview."""
    provider = get_sms_provider()
    if isinstance(provider, LocalDemoOTPProvider):
        return {
            "provider": provider.get_provider_name(),
            "dispatches": provider.get_recent_dispatches(limit=15),
        }
    return {
        "provider": provider.get_provider_name(),
        "dispatches": [],
        "note": "Active provider is an external gateway (MSG91/Twilio).",
    }


# =========================================================================
# OFFLINE TOTP (RFC 6238 AUTHENTICATOR) ENDPOINTS
# =========================================================================

@router.post(
    "/totp/enroll/setup",
    response_model=TOTPEnrollSetupResponse,
    summary="Initialize TOTP enrollment (generates Base32 secret & offline QR code)",
)
def totp_enroll_setup(
    payload: TOTPEnrollSetupRequest,
    request: Request,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    Generates a secure Base32 TOTP secret, otpauth:// URI, and Base64 QR code PNG.
    The secret is NOT enabled for login until verified with a 6-digit code.
    100% offline generation — no external QR APIs called.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    try:
        res = auth_service.setup_totp_enrollment(
            db=db,
            phone_number=payload.phone_number,
            role_hint=payload.role_hint,
            full_name=payload.full_name,
            client_ip=client_ip,
        )
        return res
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post(
    "/totp/enroll/verify",
    response_model=AuthSessionResponse,
    summary="Complete TOTP enrollment by verifying the first 6-digit code",
)
def totp_enroll_verify(
    payload: TOTPEnrollVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    Verifies the 6-digit code from Google/Microsoft Authenticator and activates TOTP for the user.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent", "Unknown")
    try:
        session_data = auth_service.verify_totp_enrollment(
            db=db,
            phone_number=payload.phone_number,
            totp_code=payload.totp_code,
            role=payload.role,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        response.set_cookie(
            key="aegis_session",
            value=session_data["session_token"],
            httponly=True,
            samesite="lax",
            max_age=AuthConfig.SESSION_EXPIRY_HOURS * 3600,
        )
        return session_data
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )



@router.post(
    "/totp/verify",
    response_model=AuthSessionResponse,
    summary="Offline TOTP login (verifies 6-digit code locally without SMS/Internet)",
)
def totp_login(
    payload: TOTPLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    Authenticates a user via standard RFC 6238 TOTP 6-digit code.
    Verified 100% locally by FastAPI backend without SMS, network, or external APIs.
    Sets session cookie and returns role-specific dashboard routing destination.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent", "Unknown")

    try:
        session_data = auth_service.verify_totp_login(
            db=db,
            phone_number=payload.phone_number,
            totp_code=payload.totp_code,
            role=payload.role,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        response.set_cookie(
            key="aegis_session",
            value=session_data["session_token"],
            httponly=True,
            samesite="lax",
            max_age=AuthConfig.SESSION_EXPIRY_HOURS * 3600,
        )

        return session_data
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get(
    "/totp/status/{phone_number}",
    response_model=TOTPStatusResponse,
    summary="Check if user has TOTP enabled",
)
def get_totp_status(
    phone_number: str,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Returns whether TOTP is enrolled and active for the specified mobile/user ID."""
    return auth_service.get_totp_status(db=db, phone_number=phone_number)


@router.get(
    "/totp/demo-code/{phone_number}",
    summary="Inspect live local TOTP code for hackathon offline demo presentation",
)
def get_demo_totp_code(
    phone_number: str,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    Helper for demonstration UI to show the current 6-digit code and remaining seconds in the 30s window.
    Allows testing when physical phone authenticator is not available or for instant comparison.
    """
    try:
        return auth_service.get_totp_current_code_for_demo(db=db, phone_number=phone_number)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

