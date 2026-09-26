import json
import secrets
from typing import Any, Dict, Optional
import urllib.error
import urllib.parse
import urllib.request

from engine.auth.providers.base import BaseSMSProvider, OTPDeliveryResult


class MSG91SMSProvider(BaseSMSProvider):
    """
    MSG91 SMS Gateway Provider.
    Implements standard MSG91 SendOTP API v5 integration.
    Falls back gracefully to offline mode if API keys are missing or offline mode is forced.
    """

    def __init__(
        self,
        auth_key: Optional[str] = None,
        template_id: Optional[str] = None,
        sender_id: Optional[str] = "AEGISR",
        demo_mode: bool = False,
    ):
        self.auth_key = auth_key
        self.template_id = template_id
        self.sender_id = sender_id
        self.demo_mode = demo_mode
        self.api_url = "https://control.msg91.com/api/v5/otp"

    def send_otp(
        self,
        phone_number: str,
        otp_code: str,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OTPDeliveryResult:
        normalized_phone = self.normalize_phone_number(phone_number, default_country_code="+91")
        
        if not self.verify_phone_number(normalized_phone):
            return OTPDeliveryResult(
                success=False,
                provider=self.get_provider_name(),
                phone_number=phone_number,
                error=f"Invalid phone number format: '{phone_number}'",
            )

        # If credentials are not present, provide a graceful fallback or simulated response
        if not self.auth_key or not self.template_id:
            msg_id = f"MSG91-MOCK-{secrets.token_hex(4).upper()}"
            return OTPDeliveryResult(
                success=True,
                provider=self.get_provider_name(),
                phone_number=normalized_phone,
                message_id=msg_id,
                preview_otp=otp_code if self.demo_mode else None,
                metadata={
                    "mode": "unconfigured_credentials_fallback",
                    "note": "MSG91 credentials not configured; simulated send completed.",
                },
            )

        # MSG91 expects mobile without '+' for Indian numbers or with country code
        clean_mobile = normalized_phone.lstrip("+")

        params = {
            "template_id": self.template_id,
            "mobile": clean_mobile,
            "otp": otp_code,
            "otp_length": len(otp_code),
            "otp_expiry": 5,  # 5 minutes
        }

        query_str = urllib.parse.urlencode(params)
        url = f"{self.api_url}?{query_str}"

        req = urllib.request.Request(
            url,
            headers={
                "authkey": self.auth_key,
                "content-type": "application/json",
                "User-Agent": "AEGIS-Rx-Safety-System/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                msg_type = resp_data.get("type")
                if msg_type == "success":
                    msg_id = resp_data.get("message", f"MSG91-{secrets.token_hex(4)}")
                    return OTPDeliveryResult(
                        success=True,
                        provider=self.get_provider_name(),
                        phone_number=normalized_phone,
                        message_id=msg_id,
                        preview_otp=otp_code if self.demo_mode else None,
                        metadata=resp_data,
                    )
                else:
                    return OTPDeliveryResult(
                        success=False,
                        provider=self.get_provider_name(),
                        phone_number=normalized_phone,
                        error=resp_data.get("message", "MSG91 returned an error"),
                        metadata=resp_data,
                    )
        except Exception as exc:
            return OTPDeliveryResult(
                success=False,
                provider=self.get_provider_name(),
                phone_number=normalized_phone,
                error=f"MSG91 API request failed: {str(exc)}",
            )

    def get_provider_name(self) -> str:
        return "msg91"

    def get_status(self) -> Dict[str, Any]:
        has_auth = bool(self.auth_key)
        has_tmpl = bool(self.template_id)
        return {
            "provider": self.get_provider_name(),
            "name": "MSG91 SMS Gateway",
            "is_offline_capable": False,
            "is_configured": has_auth and has_tmpl,
            "auth_key_configured": has_auth,
            "template_id_configured": has_tmpl,
            "status": "ready" if (has_auth and has_tmpl) else "credentials_pending",
            "capabilities": ["international_sms", "dlts_templates", "send_otp_v5"],
        }
