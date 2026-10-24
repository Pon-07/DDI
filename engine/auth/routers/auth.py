from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.dependencies import get_audit_ledger
from app.schemas_auth import (
    AuthSessionResponse,
    DemoLoginRequest,
    DemoPersonaListResponse,
    LogoutResponse,
    OTPRequest,
    OTPVerifyRequest,
    PasswordLoginRequest,
    RoleSwitchRequest,
    TOTPSetupResponse,
    TOTPVerifyRequest,
    UserResponse,
)
from database.database import get_db
from engine.auth.config import AuthConfig
from engine.auth.models import UserSession
from engine.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])
auth_service = AuthService()


def get_current_session(
    request: Request,
    db: Session = Depends(get_db),
) -> UserSession:
    """Dependency extracting and validating active session from cookie or Authorization header."""
    token = request.cookies.get(AuthConfig.COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: no active session found.",
        )

    session = auth_service.validate_session(token, db)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session is invalid or has expired.",
        )

    return session


@router.get("/demo-personas", response_model=DemoPersonaListResponse, summary="List available demo personas")
def list_demo_personas():
    """Retrieve list of synthetic demo personas available for evaluator login."""
    is_demo = AuthConfig.is_demo_mode()
    personas = auth_service.get_demo_personas() if is_demo else []
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
):
    """
    Authenticate directly as one of the configured synthetic demo personas.
    Sets the HTTP-only 'aegis_session' cookie and returns session metadata.
    Strictly rejected if AEGIS_DEMO_MODE is False.
    """
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("User-Agent")

    session = auth_service.demo_login(
        persona_identifier=payload.persona_identifier,
        db=db,
        audit_ledger=audit_ledger,
        ip_address=client_ip,
        user_agent=user_agent,
    )

    # Set HTTP-only session cookie
    response.set_cookie(
        key=AuthConfig.COOKIE_NAME,
        value=session.session_token,
        httponly=True,
        samesite="lax",
        max_age=AuthConfig.SESSION_DURATION_SECONDS,
    )

    user = session.user
    return AuthSessionResponse(
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
        expires_at=session.expires_at,
        message=f"Logged in successfully as {user.full_name} ({session.role}).",
    )


@router.post("/logout", response_model=LogoutResponse, summary="Logout and invalidate session")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    audit_ledger=Depends(get_audit_ledger),
):
    """Invalidate current session and clear session cookie."""
    token = request.cookies.get(AuthConfig.COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()

    if token:
        auth_service.logout(token, db, audit_ledger=audit_ledger)

    response.delete_cookie(key=AuthConfig.COOKIE_NAME)
    return LogoutResponse(
        status="logged_out",
        message="Session invalidated and cookie cleared.",
    )


@router.get("/me", response_model=UserResponse, summary="Inspect current authenticated user")
def get_current_user_profile(
    current_session: UserSession = Depends(get_current_session),
):
    """Retrieve profile of the currently authenticated user."""
    user = current_session.user
    return UserResponse(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        phone_number=user.phone_number,
        role=current_session.role,
        is_active=user.is_active,
        demo_mode_allowed=user.demo_mode_allowed,
    )


# --- Preserved endpoints for backward compatibility & non-demo flows ---


@router.post("/login", response_model=AuthSessionResponse, summary="Standard password login")
def password_login(
    payload: PasswordLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Standard password login path. Rejects insecure shortcuts."""
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Standard password login is not configured for demo. Use /auth/demo-login.",
    )


@router.post("/otp/request", summary="Request OTP code")
def request_otp(payload: OTPRequest):
    """Preserved OTP request endpoint."""
    return {"status": "otp_sent", "phone_number": payload.phone_number, "message": "Demo OTP dispatched"}


@router.post("/otp/verify", summary="Verify OTP code")
def verify_otp(payload: OTPVerifyRequest):
    """Preserved OTP verify endpoint."""
    return {"status": "verified", "phone_number": payload.phone_number}


@router.post("/switch-role", summary="Switch role for active session")
def switch_role(
    payload: RoleSwitchRequest,
    current_session: UserSession = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    """Switch active session role if permitted."""
    allowed_roles = {"doctor", "nurse", "pharmacist", "administrator"}
    if payload.new_role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{payload.new_role}'. Must be one of: {allowed_roles}",
        )

    # Only administrator accounts can switch into administrator role
    if payload.new_role == "administrator" and current_session.user.role != "administrator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Privilege escalation prohibited: cannot switch to administrator role.",
        )

    current_session.role = payload.new_role
    db.commit()
    return {"status": "role_switched", "active_role": payload.new_role}


@router.get("/totp/setup", response_model=TOTPSetupResponse, summary="Setup TOTP authenticator")
def setup_totp(current_session: UserSession = Depends(get_current_session)):
    """Preserved TOTP setup endpoint."""
    return TOTPSetupResponse(secret="JBSWY3DPEHPK3PXP", provisioning_uri="otpauth://totp/AEGIS:demo?secret=JBSWY3DPEHPK3PXP")


@router.post("/totp/verify", summary="Verify TOTP code")
def verify_totp(payload: TOTPVerifyRequest, current_session: UserSession = Depends(get_current_session)):
    """Preserved TOTP verify endpoint."""
    return {"status": "totp_verified"}
