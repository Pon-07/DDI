from typing import Any, Dict, List, Optional

from engine.auth.config import AuthConfig
from engine.auth.providers.base import BaseSMSProvider
from engine.auth.providers.local import LocalDemoOTPProvider
from engine.auth.providers.msg91 import MSG91SMSProvider
from engine.auth.providers.twilio import TwilioSMSProvider

_PROVIDER_INSTANCES: Dict[str, BaseSMSProvider] = {}
_ACTIVE_PROVIDER_NAME: str = AuthConfig.SMS_PROVIDER


def get_sms_provider(
    provider_name: Optional[str] = None,
    demo_mode: Optional[bool] = None,
    refresh: bool = False,
) -> BaseSMSProvider:
    """
    Factory to retrieve or instantiate the appropriate SMS provider.
    Supports dynamic runtime switching.
    """
    global _ACTIVE_PROVIDER_NAME

    target = (provider_name or _ACTIVE_PROVIDER_NAME or "local").lower().strip()
    is_demo = AuthConfig.DEMO_MODE if demo_mode is None else demo_mode

    # Cache key based on provider and demo_mode
    cache_key = f"{target}_{is_demo}"

    if not refresh and cache_key in _PROVIDER_INSTANCES:
        return _PROVIDER_INSTANCES[cache_key]

    provider: BaseSMSProvider
    if target in ("local", "local_demo", "offline"):
        provider = LocalDemoOTPProvider(demo_mode=is_demo)
    elif target == "msg91":
        provider = MSG91SMSProvider(
            auth_key=AuthConfig.MSG91_AUTH_KEY,
            template_id=AuthConfig.MSG91_TEMPLATE_ID,
            sender_id=AuthConfig.MSG91_SENDER_ID,
            demo_mode=is_demo,
        )
    elif target == "twilio":
        provider = TwilioSMSProvider(
            account_sid=AuthConfig.TWILIO_ACCOUNT_SID,
            auth_token=AuthConfig.TWILIO_AUTH_TOKEN,
            from_phone=AuthConfig.TWILIO_FROM_PHONE,
            demo_mode=is_demo,
        )
    else:
        # Default fallback to safe offline provider
        provider = LocalDemoOTPProvider(demo_mode=is_demo)

    _PROVIDER_INSTANCES[cache_key] = provider
    return provider


def set_active_sms_provider(provider_name: str) -> BaseSMSProvider:
    """Dynamically set the default active SMS gateway."""
    global _ACTIVE_PROVIDER_NAME
    _ACTIVE_PROVIDER_NAME = provider_name.lower().strip()
    return get_sms_provider(_ACTIVE_PROVIDER_NAME, refresh=True)


def list_available_providers() -> List[Dict[str, Any]]:
    """Return status and configurations for all supported SMS providers."""
    providers = [
        LocalDemoOTPProvider(demo_mode=AuthConfig.DEMO_MODE),
        MSG91SMSProvider(
            auth_key=AuthConfig.MSG91_AUTH_KEY,
            template_id=AuthConfig.MSG91_TEMPLATE_ID,
            demo_mode=AuthConfig.DEMO_MODE,
        ),
        TwilioSMSProvider(
            account_sid=AuthConfig.TWILIO_ACCOUNT_SID,
            auth_token=AuthConfig.TWILIO_AUTH_TOKEN,
            from_phone=AuthConfig.TWILIO_FROM_PHONE,
            demo_mode=AuthConfig.DEMO_MODE,
        ),
    ]

    results = []
    for p in providers:
        status = p.get_status()
        status["is_active"] = (p.get_provider_name() == _ACTIVE_PROVIDER_NAME)
        results.append(status)
    return results
