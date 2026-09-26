import base64
import json
import secrets
from typing import Any, Dict, Optional
import urllib.error
import urllib.parse
import urllib.request

from engine.auth.providers.base import BaseSMSProvider, OTPDeliveryResult


class TwilioSMSProvider(BaseSMSProvider):
    """
    Twilio SMS Gateway Provider.
    Implements Twilio REST Messages API integration.
    Falls back gracefully to offline simulation if credentials are unconfigured or offline mode is forced.
    """

    def __init__(
        self,
        account_sid: Optional[str] = None,
        auth_token: Optional[str] = None,
        from_phone: Optional[str] = None,
        demo_mode: bool = False,
    ):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_phone = from_phone
        self.demo_mode = demo_mode

    def send_otp(
        self,
        phone_number: str,
        otp_code: str,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OTPDeliveryResult:
        normalized_phone = self.normalize_phone_number(phone_number, default_country_code="+1")

        if not self.verify_phone_number(normalized_phone):
            return OTPDeliveryResult(
                success=False,
                provider=self.get_provider_name(),
                phone_number=phone_number,
                error=f"Invalid phone number format: '{phone_number}'",
            )

        # If credentials are not set, return graceful simulated mock
        if not self.account_sid or not self.auth_token or not self.from_phone:
            msg_id = f"TWILIO-MOCK-{secrets.token_hex(4).upper()}"
            return OTPDeliveryResult(
                success=True,
                provider=self.get_provider_name(),
                phone_number=normalized_phone,
                message_id=msg_id,
                preview_otp=otp_code if self.demo_mode else None,
                metadata={
                    "mode": "unconfigured_credentials_fallback",
                    "note": "Twilio credentials not configured; simulated send completed.",
                },
            )

        body_text = message or (
            f"[AEGIS Rx] Your clinical login verification code is {otp_code}. "
            f"Expires in 5 minutes. Do not share."
        )

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        data = urllib.parse.urlencode({
            "To": normalized_phone,
            "From": self.from_phone,
            "Body": body_text,
        }).encode("utf-8")

        auth_str = f"{self.account_sid}:{self.auth_token}"
        encoded_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Basic {encoded_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "AEGIS-Rx-Safety-System/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                sid = resp_data.get("sid", f"TWILIO-{secrets.token_hex(4)}")
                return OTPDeliveryResult(
                    success=True,
                    provider=self.get_provider_name(),
                    phone_number=normalized_phone,
                    message_id=sid,
                    preview_otp=otp_code if self.demo_mode else None,
                    metadata=resp_data,
                )
        except Exception as exc:
            return OTPDeliveryResult(
                success=False,
                provider=self.get_provider_name(),
                phone_number=normalized_phone,
                error=f"Twilio API request failed: {str(exc)}",
            )

    def get_provider_name(self) -> str:
        return "twilio"

    def get_status(self) -> Dict[str, Any]:
        has_creds = bool(self.account_sid and self.auth_token and self.from_phone)
        return {
            "provider": self.get_provider_name(),
            "name": "Twilio Programmable SMS",
            "is_offline_capable": False,
            "is_configured": has_creds,
            "account_sid_configured": bool(self.account_sid),
            "from_phone": self.from_phone or "not_configured",
            "status": "ready" if has_creds else "credentials_pending",
            "capabilities": ["global_sms", "rest_api", "programmable_messaging"],
        }
