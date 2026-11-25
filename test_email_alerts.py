"""Mocked tests for the real alert-email formatting and SMTP send path.

SMTP is replaced with an in-memory fake: these tests never contact Gmail.
"""
import os
import unittest
from unittest.mock import patch

from email_alerts import send_alert


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.login_args = None
        self.messages = []
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def login(self, sender, password):
        self.login_args = (sender, password)

    def send_message(self, message):
        self.messages.append(message)


class EmailAlertTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances.clear()
        self.env = patch.dict(os.environ, {
            "GMAIL_SENDER": "sender@example.test",
            "GMAIL_APP_PASSWORD": "not-a-real-password",
            "ALERT_TO": "recipient@example.test",
        }, clear=True)
        self.env.start()
        self.smtp_patch = patch("email_alerts.smtplib.SMTP_SSL", FakeSMTP)
        self.smtp_patch.start()

    def tearDown(self):
        self.smtp_patch.stop()
        self.env.stop()

    def test_test_mode_uses_mock_smtp_and_sends_explicit_test_message(self):
        signal = {
            "symbol": "EUR/USD", "action": "BUY", "price": 1.085,
            "stop_loss": 1.080, "take_profit_1": 1.090,
            "take_profit_2": 1.095, "risk_reward": 2.0,
            "swing_trend_1h": "bullish", "internal_trend_1h": "bullish",
            "zone_15m": {"side": "demand", "low": 1.081, "high": 1.083},
            "liquidity_sweep": "sell-side", "market_shift": "bullish",
            "candle_time": "2026-11-25T12:00:00+00:00",
            "valid_until": "2026-11-25T12:10:00+00:00",
            "reason": "Mocked test signal", "hold_hours": 5,
        }

        send_alert(signal, test_mode=True)

        self.assertEqual(len(FakeSMTP.instances), 1)
        smtp = FakeSMTP.instances[0]
        self.assertEqual((smtp.host, smtp.port), ("smtp.gmail.com", 465))
        self.assertEqual(smtp.timeout, 25)
        self.assertEqual(smtp.login_args, ("sender@example.test", "not-a-real-password"))
        self.assertEqual(len(smtp.messages), 1)
        message = smtp.messages[0]
        self.assertEqual(message["To"], "recipient@example.test")
        self.assertIn("TEST ONLY", message["Subject"])
        self.assertIn("TEST ONLY — NO TRADE", message.get_content())
        self.assertIn("No broker order was placed", message.get_content())

    def test_normal_alert_contains_entry_stop_targets_and_risk_rules(self):
        signal = {
            "symbol": "EUR/USD", "action": "SELL", "price": 1.085,
            "entry_zone_low": 1.084, "entry_zone_high": 1.086,
            "stop_loss": 1.090, "take_profit_1": 1.080,
            "take_profit_2": 1.075, "risk_reward": 2.0,
            "swing_trend_1h": "bearish", "internal_trend_1h": "bearish",
            "trend_aligned": True,
            "zone_15m": {"side": "supply", "low": 1.087, "high": 1.089},
            "liquidity_sweep": "buy-side", "market_shift": "bearish",
            "candle_time": "2026-11-25T12:00:00+00:00",
            "valid_until": "2026-11-25T12:10:00+00:00",
            "reason": "Mocked confirmed setup", "hold_hours": 5,
        }

        send_alert(signal)

        self.assertEqual(len(FakeSMTP.instances), 1)
        message = FakeSMTP.instances[0].messages[0]
        body = message.get_content()
        self.assertIn("SELL: EUR/USD", message["Subject"])
        for expected in ("Entry reference:", "Stop-loss:", "TP1 (1R):",
                         "TP2 / opposing liquidity:", "Risk-to-reward:",
                         "Suggested review/holding window:", "RISK RULES:",
                         "The bot does not place or manage orders."):
            self.assertIn(expected, body)


if __name__ == "__main__":
    unittest.main()
