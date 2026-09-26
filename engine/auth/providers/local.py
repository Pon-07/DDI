from collections import deque
from datetime import datetime
import secrets
from typing import Any, Dict, List, Optional

from engine.auth.providers.base import BaseSMSProvider, OTPDeliveryResult


class LocalDemoOTPProvider(BaseSMSProvider):
    """
    Offline Local & Demo OTP Provider.
    Operates 100% offline with zero external network dependencies.
    Simulates realistic SMS delivery and maintains an in-memory dispatch inbox
    for hackathon demonstration and testing.
    """

    def __init__(self, demo_mode: bool = True, max_history: int = 50):
        self.demo_mode = demo_mode
        self._history: deque = deque(maxlen=max_history)
        self._latest_by_phone: Dict[str, Dict[str, Any]] = {}

    def send_otp(
        self,
        phone_number: str,
        otp_code: str,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OTPDeliveryResult:
        normalized_phone = self.normalize_phone_number(phone_number)
        
        if not self.verify_phone_number(normalized_phone):
            return OTPDeliveryResult(
                success=False,
                provider=self.get_provider_name(),
                phone_number=phone_number,
                error=f"Invalid phone number format: '{phone_number}'",
            )

        msg_id = f"LOCAL-SMS-{secrets.token_hex(4).upper()}"
        formatted_message = message or (
            f"[AEGIS Rx Safety Guard] Your clinical verification code is {otp_code}. "
            f"Valid for 5 minutes. Do not share this code."
        )

        record = {
            "message_id": msg_id,
            "phone_number": normalized_phone,
            "otp_code": otp_code,
            "formatted_message": formatted_message,
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "delivery_status": "DELIVERED_OFFLINE",
            "metadata": metadata or {},
        }

        self._history.append(record)
        self._latest_by_phone[normalized_phone] = record

        # Only provide preview_otp if demo_mode is True
        preview = otp_code if self.demo_mode else None

        return OTPDeliveryResult(
            success=True,
            provider=self.get_provider_name(),
            phone_number=normalized_phone,
            message_id=msg_id,
            preview_otp=preview,
            metadata={
                "delivery_channel": "offline_local_simulation",
                "formatted_message": formatted_message,
                "offline_ready": True,
            },
        )

    def get_provider_name(self) -> str:
        return "local_demo"

    def get_status(self) -> Dict[str, Any]:
        return {
            "provider": self.get_provider_name(),
            "name": "Local Offline / Demo Provider",
            "is_offline_capable": True,
            "demo_mode": self.demo_mode,
            "dispatches_count": len(self._history),
            "status": "ready",
            "capabilities": ["offline_simulation", "inbox_preview", "zero_external_calls"],
        }

    def get_recent_dispatches(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return the most recent simulated SMS dispatches."""
        items = list(self._history)
        items.reverse()
        return items[:limit]

    def get_last_otp(self, phone_number: str) -> Optional[str]:
        """Retrieve the latest OTP for a phone number (used in demo testing)."""
        normalized = self.normalize_phone_number(phone_number)
        record = self._latest_by_phone.get(normalized)
        return record.get("otp_code") if record else None

    def clear_history(self) -> None:
        """Clear the in-memory dispatch history."""
        self._history.clear()
        self._latest_by_phone.clear()
