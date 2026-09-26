from typing import Callable, List, Optional, Tuple
from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from agents.audit.ledger import AuditLedger
from app.dependencies import get_audit_ledger
from database.database import get_db
from engine.auth.models import User, UserSession
from engine.auth.providers.factory import get_sms_provider
from engine.auth.service import AuthService

_default_auth_service: Optional[AuthService] = None


def get_auth_service(
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
) -> AuthService:
    """Dependency provider for AuthService instance."""
    global _default_auth_service
    if _default_auth_service is None or _default_auth_service.audit_ledger is not audit_ledger:
        _default_auth_service = AuthService(
            audit_ledger=audit_ledger,
            sms_provider=get_sms_provider(),
        )
    return _default_auth_service


def get_session_token_from_request(
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None),
    aegis_session: Optional[str] = Cookie(None),
) -> Optional[str]:
    """Extract session token from Bearer header, X-Session-Token header, or cookie."""
    if authorization and authorization.startswith("Bearer "):
        return authorization.replace("Bearer ", "").strip()
    if x_session_token:
        return x_session_token.strip()
    if aegis_session:
        return aegis_session.strip()
    return None


def get_current_user(
    token: Optional[str] = Depends(get_session_token_from_request),
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> User:
    """FastAPI dependency to require an authenticated active user."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in with your mobile OTP.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    res = auth_service.validate_session(db, token)
    if not res:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _, user = res
    return user


def get_optional_user(
    token: Optional[str] = Depends(get_session_token_from_request),
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> Optional[User]:
    """FastAPI dependency to retrieve user if authenticated, without raising 401."""
    if not token:
        return None
    res = auth_service.validate_session(db, token)
    return res[1] if res else None


def require_role(allowed_roles: List[str]) -> Callable:
    """Factory dependency to enforce role-based access control (RBAC)."""
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access forbidden: User role '{current_user.role}' lacks permission. Required roles: {allowed_roles}",
            )
        return current_user

    return role_checker
