from engine.auth.config import AuthConfig, DEMO_PERSONAS, SUPPORTED_ROLES
from engine.auth.dependencies import (
    get_auth_service,
    get_current_user,
    get_optional_user,
    require_role,
)
from engine.auth.models import AuthBase, OTPRecord, User, UserSession
from engine.auth.providers import (
    BaseSMSProvider,
    LocalDemoOTPProvider,
    MSG91SMSProvider,
    OTPDeliveryResult,
    TwilioSMSProvider,
    get_sms_provider,
    list_available_providers,
    set_active_sms_provider,
)
from engine.auth.service import AuthService

__all__ = [
    "AuthConfig",
    "SUPPORTED_ROLES",
    "DEMO_PERSONAS",
    "AuthBase",
    "User",
    "UserSession",
    "OTPRecord",
    "BaseSMSProvider",
    "OTPDeliveryResult",
    "LocalDemoOTPProvider",
    "MSG91SMSProvider",
    "TwilioSMSProvider",
    "get_sms_provider",
    "set_active_sms_provider",
    "list_available_providers",
    "AuthService",
    "get_auth_service",
    "get_current_user",
    "get_optional_user",
    "require_role",
]
