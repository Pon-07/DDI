from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any, Dict, Optional


@dataclass
class OTPDeliveryResult:
    """Standardized delivery result returned by all SMS/OTP providers."""
    success: bool
    provider: str
    phone_number: str
    message_id: Optional[str] = None
    error: Optional[str] = None
    preview_otp: Optional[str] = None  # Populated only in local/demo mode for offline UI preview
    timestamp: str = field(default_factory=lambda: datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "provider": self.provider,
            "phone_number": self.phone_number,
            "message_id": self.message_id,
            "error": self.error,
            "preview_otp": self.preview_otp,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class BaseSMSProvider(ABC):
    """
    Abstract Base Class for SMS and OTP Delivery Gateways.
    Enables seamless offline local demo operation while allowing MSG91, Twilio,
    or other enterprise SMS providers to be plugged in without changing business logic.
    """

    @abstractmethod
    def send_otp(
        self,
        phone_number: str,
        otp_code: str,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OTPDeliveryResult:
        """
        Send a one-time passcode to the specified phone number.
        Returns a standardized OTPDeliveryResult.
        """
        pass

    def verify_phone_number(self, phone_number: str) -> bool:
        """
        Basic sanity check for E.164 or standard 10-15 digit phone format.
        """
        if not phone_number or not isinstance(phone_number, str):
            return False
        # Remove whitespace, dashes, parens
        cleaned = re.sub(r"[\s\-\(\)\.]", "", phone_number)
        # Check if starts with optional + and has 10 to 15 digits
        return bool(re.match(r"^\+?[0-9]{10,15}$", cleaned))

    def normalize_phone_number(self, phone_number: str, default_country_code: str = "+1") -> str:
        """
        Normalize mobile numbers to clean E.164-like standard format.
        """
        cleaned = re.sub(r"[\s\-\(\)\.]", "", phone_number.strip())
        if cleaned.startswith("+"):
            return cleaned
        if len(cleaned) == 10:
            return f"{default_country_code}{cleaned}"
        if len(cleaned) == 11 and cleaned.startswith("1"):
            return f"+{cleaned}"
        if len(cleaned) == 12 and cleaned.startswith("91"):
            return f"+{cleaned}"
        return f"+{cleaned}"

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the unique identifier string for this provider."""
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Return health status and capabilities of this provider."""
        pass
