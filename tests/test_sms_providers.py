import unittest
from engine.auth.providers.base import BaseSMSProvider, OTPDeliveryResult
from engine.auth.providers.factory import (
    get_sms_provider,
    list_available_providers,
    set_active_sms_provider,
)
from engine.auth.providers.local import LocalDemoOTPProvider
from engine.auth.providers.msg91 import MSG91SMSProvider
from engine.auth.providers.twilio import TwilioSMSProvider


class TestSMSProviders(unittest.TestCase):
    """
    Tests for pluggable SMS Gateway provider abstraction:
    - BaseSMSProvider abstraction contract
    - LocalDemoOTPProvider (100% offline simulation & dispatch inbox)
    - MSG91SMSProvider (integration & offline fallback)
    - TwilioSMSProvider (integration & offline fallback)
    - Provider Factory and dynamic runtime switching
    """

    def test_local_demo_provider_offline_simulation(self):
        """Verify local demo provider sends simulated SMS and captures in-memory history."""
        provider = LocalDemoOTPProvider(demo_mode=True)
        res = provider.send_otp(
            phone_number="+15550192831",
            otp_code="849201",
        )

        self.assertTrue(res.success)
        self.assertEqual(res.provider, "local_demo")
        self.assertEqual(res.preview_otp, "849201")
        self.assertIsNotNone(res.message_id)

        # Check dispatch history
        history = provider.get_recent_dispatches()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["otp_code"], "849201")
        self.assertEqual(history[0]["phone_number"], "+15550192831")

        # Check latest OTP helper
        self.assertEqual(provider.get_last_otp("+15550192831"), "849201")

    def test_local_provider_non_demo_mode_masks_preview(self):
        """Verify local provider in non-demo mode does not leak preview_otp in result."""
        provider = LocalDemoOTPProvider(demo_mode=False)
        res = provider.send_otp(
            phone_number="+15550192832",
            otp_code="392810",
        )
        self.assertTrue(res.success)
        self.assertIsNone(res.preview_otp)

    def test_invalid_phone_number_handling(self):
        """Verify provider rejects invalid phone numbers."""
        provider = LocalDemoOTPProvider()
        res = provider.send_otp(
            phone_number="not-a-number",
            otp_code="123456",
        )
        self.assertFalse(res.success)
        self.assertIn("Invalid phone number", res.error)

    def test_msg91_provider_contract_and_fallback(self):
        """Verify MSG91 provider implements interface and handles unconfigured credentials smoothly."""
        msg91 = MSG91SMSProvider(auth_key=None, template_id=None, demo_mode=True)
        self.assertEqual(msg91.get_provider_name(), "msg91")

        status = msg91.get_status()
        self.assertFalse(status["is_configured"])
        self.assertEqual(status["status"], "credentials_pending")

        # Sending without credentials returns mock fallback in demo mode
        res = msg91.send_otp(
            phone_number="+919876543210",
            otp_code="582910",
        )
        self.assertTrue(res.success)
        self.assertEqual(res.provider, "msg91")
        self.assertEqual(res.metadata.get("mode"), "unconfigured_credentials_fallback")

    def test_twilio_provider_contract_and_fallback(self):
        """Verify Twilio provider implements interface and handles unconfigured credentials smoothly."""
        twilio = TwilioSMSProvider(account_sid=None, auth_token=None, from_phone=None, demo_mode=True)
        self.assertEqual(twilio.get_provider_name(), "twilio")

        status = twilio.get_status()
        self.assertFalse(status["is_configured"])
        self.assertEqual(status["status"], "credentials_pending")

        # Sending without credentials returns mock fallback
        res = twilio.send_otp(
            phone_number="+15550192834",
            otp_code="492019",
        )
        self.assertTrue(res.success)
        self.assertEqual(res.provider, "twilio")
        self.assertEqual(res.metadata.get("mode"), "unconfigured_credentials_fallback")

    def test_provider_factory_and_dynamic_switching(self):
        """Verify factory can dynamically switch active providers at runtime."""
        # Switch to local
        p_local = set_active_sms_provider("local")
        self.assertEqual(p_local.get_provider_name(), "local_demo")
        self.assertEqual(get_sms_provider().get_provider_name(), "local_demo")

        # Switch to msg91
        p_msg91 = set_active_sms_provider("msg91")
        self.assertEqual(p_msg91.get_provider_name(), "msg91")
        self.assertEqual(get_sms_provider().get_provider_name(), "msg91")

        # Switch to twilio
        p_twilio = set_active_sms_provider("twilio")
        self.assertEqual(p_twilio.get_provider_name(), "twilio")
        self.assertEqual(get_sms_provider().get_provider_name(), "twilio")

        # Reset back to local
        set_active_sms_provider("local")
        self.assertEqual(get_sms_provider().get_provider_name(), "local_demo")

        # List all providers
        providers = list_available_providers()
        self.assertEqual(len(providers), 3)
        prov_names = {p["provider"] for p in providers}
        self.assertIn("local_demo", prov_names)
        self.assertIn("msg91", prov_names)
        self.assertIn("twilio", prov_names)


if __name__ == "__main__":
    unittest.main()
