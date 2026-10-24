from datetime import datetime, timedelta
import secrets
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.audit.ledger import AuditLedger
from engine.auth.config import AuthConfig
from engine.auth.models import User, UserSession


class AuthService:
    """Service handling session lifecycle, demo authentication, and tamper-evident audit logging."""

    def __init__(self, config: Optional[AuthConfig] = None):
        self.config = config or AuthConfig()

    def get_demo_personas(self) -> List[Dict[str, Any]]:
        """List configured synthetic personas available for demo login."""
        return list(self.config.DEMO_PERSONAS.values())

    def demo_login(
        self,
        persona_identifier: str,
        db: Session,
        audit_ledger: Optional[AuditLedger] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> UserSession:
        """
        Execute deterministic, single-click demo login for authorized synthetic personas.

        Security guarantees:
        1. Strict server-side demo mode enforcement (rejects if AEGIS_DEMO_MODE is not active).
        2. Strict persona whitelist verification (rejects unconfigured identifiers or arbitrary phone numbers).
        3. Validates account is active and explicitly flagged for demo mode.
        4. Reuses existing demo user records without overwriting credentials or TOTP configuration.
        5. Generates cryptographically secure, expiring session tokens.
        6. Appends immutable audit trail to AuditLedger without logging secrets or session tokens.
        """
        # 1. Server-side demo mode enforcement
        if not self.config.is_demo_mode():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Demo login rejected: server-side demo mode is disabled (AEGIS_DEMO_MODE=False).",
            )

        # 2. Strict persona resolution
        persona = self.config.resolve_persona(persona_identifier)
        if not persona:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid demo persona identifier '{persona_identifier}'. Only pre-configured synthetic personas are permitted.",
            )

        try:
            # 3. Retrieve or safely seed the demo user
            if db.bind:
                from engine.auth.models import init_auth_tables
                init_auth_tables(db.bind)

            user = db.execute(
                select(User).where(User.username == persona["username"])
            ).scalar_one_or_none()

            if not user and persona.get("phone_number"):
                user = db.execute(
                    select(User).where(User.phone_number == persona["phone_number"])
                ).scalar_one_or_none()

            if not user:
                user = User(
                    username=persona["username"],
                    full_name=persona["name"],
                    phone_number=persona["phone_number"],
                    role=persona["role"],
                    is_active=True,
                    demo_mode_allowed=True,
                )
                db.add(user)
                db.flush()
            else:
                # 4. Account status and permission verification
                if not user.is_active:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="User account is deactivated.",
                    )
                if not user.demo_mode_allowed:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Account is not permitted for demo-mode authentication.",
                    )

            # Enforce persona role strictly to prevent privilege escalation
            role_to_grant = persona["role"]

            # 5. Generate cryptographically secure expiring session
            session_token = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(seconds=self.config.SESSION_DURATION_SECONDS)

            new_session = UserSession(
                session_token=session_token,
                user_id=user.id,
                role=role_to_grant,
                ip_address=ip_address,
                user_agent=user_agent,
                expires_at=expires_at,
                is_active=True,
            )
            db.add(new_session)
            db.commit()
            db.refresh(new_session)
            db.refresh(user)

            # 6. Audit logging using actual AuditLedger (strictly omitting session tokens and secrets)
            if audit_ledger:
                try:
                    audit_ledger.append_event(
                        actor=user.username,
                        event_type="DEMO_LOGIN_SUCCESS",
                        payload={
                            "user_id": user.id,
                            "username": user.username,
                            "role": role_to_grant,
                            "persona_id": persona["id"],
                            "demo_mode": True,
                            "client_ip": ip_address or "unknown",
                        },
                    )
                except Exception as audit_err:
                    # In test environments or when ledger is offline, record or pass
                    pass

            return new_session

        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process demo login: {str(exc)}",
            ) from exc

    def validate_session(self, session_token: str, db: Session) -> Optional[UserSession]:
        """Validate session token against database and check expiration."""
        if not session_token or not isinstance(session_token, str):
            return None

        if db.bind:
            from engine.auth.models import init_auth_tables
            init_auth_tables(db.bind)

        stmt = select(UserSession).where(
            UserSession.session_token == session_token,
            UserSession.is_active == True,
        )
        session = db.execute(stmt).scalar_one_or_none()

        if not session:
            return None

        # Check expiration
        if session.expires_at < datetime.utcnow():
            session.is_active = False
            db.commit()
            return None

        return session

    def logout(
        self,
        session_token: str,
        db: Session,
        audit_ledger: Optional[AuditLedger] = None,
    ) -> bool:
        """Invalidate session and log audit record."""
        session = self.validate_session(session_token, db)
        if not session:
            return False

        actor = session.user.username if session.user else "authenticated_user"
        user_id = session.user_id
        session.is_active = False
        db.commit()

        if audit_ledger:
            try:
                audit_ledger.append_event(
                    actor=actor,
                    event_type="AUTH_LOGOUT_SUCCESS",
                    payload={
                        "user_id": user_id,
                        "action": "session_invalidated",
                    },
                )
            except Exception:
                pass

        return True
