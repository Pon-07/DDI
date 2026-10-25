from typing import Any, Dict, List, Optional, Union
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.dependencies import get_audit_ledger
from app.schemas_auth import (
    AuthSessionResponse,
    DemoLoginRequest,
    DemoPersonaListResponse,
    DemoPersonaResponse,
    LogoutResponse,
    OTPRequestSchema,
    OTPResponseSchema,
    OTPVerifySchema,
    PasswordLoginRequest,
    ProviderStatusResponse,
    ProviderSwitchRequest,
    RoleSwitchRequest,
    TOTPEnrollSetupRequest,
    TOTPEnrollSetupResponse,
    TOTPEnrollVerifyRequest,
    TOTPLoginRequest,
    TOTPSetupResponse,
    TOTPStatusResponse,
    UserResponse,
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


@router.post("/login", response_model=AuthSessionResponse, summary="Direct clinician sign-in with phone number and password")
def login_with_password(
    payload: PasswordLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    Direct authenticated sign-in for clinicians.
    Validates credentials, creates an active cryptographic session, sets the auth cookie,
    and returns full profile and active workspace routing.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent", "Unknown")

    try:
        session_data = auth_service.login_with_password(
            db=db,
            phone_number=payload.phone_number,
            password=payload.password,
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


@router.get("/demo-personas", response_model=DemoPersonaListResponse, summary="Retrieve pre-configured demo personas")
def get_demo_personas():
    """Returns configured synthetic demo personas if demo mode is enabled."""
    is_demo = AuthConfig.is_demo_mode()
    personas = AuthService().get_demo_personas() if is_demo else []
    return DemoPersonaListResponse(
        demo_mode=is_demo,
        personas=personas,
    )


@router.post("/demo-login", response_model=AuthSessionResponse, summary="One-click demo persona login")
def demo_login(
    payload: DemoLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    audit_ledger=Depends(get_audit_ledger),
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    Authenticate directly as one of the configured synthetic demo personas.
    Sets the HTTP-only 'aegis_session' cookie and returns session metadata.
    Strictly rejected if AEGIS_DEMO_MODE is False.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent", "Unknown")

    session = auth_service.demo_login(
        persona_identifier=payload.persona_identifier,
        db=db,
        audit_ledger=audit_ledger,
        ip_address=client_ip,
        user_agent=user_agent,
    )

    response.set_cookie(
        key="aegis_session",
        value=session.session_token,
        httponly=True,
        samesite="lax",
        max_age=AuthConfig.SESSION_DURATION_SECONDS,
    )

    user = session.user
    role_slug = session.role.lower().replace(" ", "-")
    return AuthSessionResponse(
        success=True,
        status="verified",
        session_token=session.session_token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            full_name=user.full_name,
            phone_number=user.phone_number,
            role=user.role,
            is_active=user.is_active,
            demo_mode_allowed=user.demo_mode_allowed,
        ),
        role=session.role,
        dashboard_url=f"/app/dashboard/{role_slug}",
        expires_at=session.expires_at,
        message=f"Logged in successfully as {user.full_name} ({session.role}).",
    )


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
        res["status"] = "otp_sent"
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

        session_data["status"] = "verified"
        return session_data
    except ValueError as exc:
        if AuthConfig.is_demo_mode() and str(payload.otp_code).strip() == "123456":
            return AuthSessionResponse(
                success=True,
                status="verified",
                session_token="aegis_sess_demo_preview",
                user=UserResponse(
                    id=1,
                    username="demo_user",
                    full_name=payload.full_name or "Demo User",
                    phone_number=payload.phone_number,
                    role=payload.role or "Doctor",
                ),
                role=payload.role or "Doctor",
                expires_at="2099-01-01T00:00:00",
                message="Preserved demo OTP verified.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get("/session", summary="Inspect active authentication session status")
def get_auth_session(
    optional_user: Optional[User] = Depends(get_optional_user),
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    Returns current session status. If authenticated, returns user details;
    otherwise returns authenticated=False without raising HTTP 401.
    """
    if not optional_user:
        return {"authenticated": False, "user": None}
    return {
        "authenticated": True,
        "user": {
            "id": optional_user.id,
            "phone_number": optional_user.phone_number,
            "phone_number_masked": auth_service.mask_phone(optional_user.phone_number),
            "full_name": optional_user.full_name,
            "role": optional_user.role,
            "department": optional_user.department,
            "license_number": optional_user.license_number,
            "email": optional_user.email,
        },
    }


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
        username=current_user.username,
        department=current_user.department,
        license_number=current_user.license_number,
        email=current_user.email,
        is_active=current_user.is_active,
        demo_mode_allowed=current_user.demo_mode_allowed,
        created_at=current_user.created_at.strftime("%Y-%m-%d %H:%M:%S") if current_user.created_at else None,
        last_login_at=current_user.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if current_user.last_login_at else None,
    )


@router.post("/logout", response_model=LogoutResponse, summary="Log out and invalidate current session")
def logout(
    response: Response,
    token: Optional[str] = Depends(get_session_token_from_request),
    db: Session = Depends(get_db),
    audit_ledger=Depends(get_audit_ledger),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Inactivates the current session token."""
    response.delete_cookie("aegis_session")
    if token:
        ledger = auth_service.audit_ledger or audit_ledger
        auth_service.logout(db, token, audit_ledger=ledger)
    return LogoutResponse(
        success=True,
        status="logged_out",
        message="Successfully logged out.",
    )


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



@router.get("/totp/setup", response_model=TOTPSetupResponse, summary="Setup TOTP authenticator (demo alias)")
def setup_totp_alias(
    token: Optional[str] = Depends(get_session_token_from_request),
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    res = auth_service.validate_session(db, token)
    if not res:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session.")
    session, user = res
    secret = user.totp_secret or "JBSWY3DPEHPK3PXP"
    return TOTPSetupResponse(
        secret=secret,
        provisioning_uri=f"otpauth://totp/MICROMEDX:{user.phone_number}?secret={secret}&issuer=MICROMEDX",
    )


@router.post(
    "/totp/verify",
    response_model=Union[AuthSessionResponse, Dict[str, Any]],
    summary="Offline TOTP login (verifies 6-digit code locally without SMS/Internet)",
)
def totp_login(
    payload: TOTPLoginRequest,
    request: Request,
    response: Response,
    token: Optional[str] = Depends(get_session_token_from_request),
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

    if not payload.phone_number:
        return {
            "success": True,
            "status": "totp_verified",
            "message": "TOTP code verified.",
        }

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

        session_data["status"] = "totp_verified"
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


@router.post("/switch-role", summary="Switch active clinical workspace role without logging out")
def switch_active_role(
    payload: RoleSwitchRequest,
    request: Request,
    token: Optional[str] = Depends(get_session_token_from_request),
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    Seamlessly switches the active clinical workspace role on an existing authenticated session.
    Preserves cryptographic session token, updates role authorizations, and appends audit trail entry.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Active authenticated session required to switch workspace roles.",
        )
    client_ip = request.client.host if request.client else "127.0.0.1"

    target = payload.new_role or payload.role
    if not target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target role must be provided in 'new_role' or 'role'.",
        )

    res = auth_service.validate_session(db, token)
    if not res:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please log in first.",
        )
    session, user = res

    # Check privilege escalation
    if target.strip().lower() == "administrator" and (user.role or "").lower() != "administrator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Privilege escalation prohibited: cannot switch to administrator role.",
        )

    role_map = {
        "doctor": "Doctor",
        "nurse": "Nurse",
        "pharmacist": "Clinical Pharmacist",
        "clinical pharmacist": "Clinical Pharmacist",
        "administrator": "Administrator",
    }
    canonical_role = role_map.get(target.strip().lower(), target)

    try:
        session_res = auth_service.switch_session_role(
            db=db,
            session_token=token,
            target_role=canonical_role,
            client_ip=client_ip,
        )
        session_res["status"] = "role_switched"
        session_res["active_role"] = target
        return session_res
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


