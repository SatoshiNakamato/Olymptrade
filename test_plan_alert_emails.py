"""Mocked SMTP tests for TP1 and plan-exit notification emails."""
import os
import unittest
from unittest.mock import patch

from email_alerts import send_exit_alert, send_tp1_alert


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.login_args, self.messages = None, []
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def login(self, sender, password):
        self.login_args = (sender, password)

    def send_message(self, message):
        self.messages.append(message)


class PlanAlertEmailTests(unittest.TestCase):
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
        self.plan = {
            "action": "BUY", "price": 1.1000, "stop_loss": 1.0900,
            "take_profit_1": 1.1100, "take_profit_2": 1.1200,
        }

    def tearDown(self):
        self.smtp_patch.stop()
        self.env.stop()

    def test_tp1_email_contains_level_and_manual_action_limits(self):
        send_tp1_alert("EUR/USD", "BUY", 1.1100, self.plan,
                       "2026-11-25T12:05:00+00:00")
        message = FakeSMTP.instances[0].messages[0]
        body = message.get_content()
        self.assertIn("TP1 REACHED: EUR/USD", message["Subject"])
        self.assertIn("TP1 level touched: 1.11000", body)
        self.assertIn("cannot change stops", body)
        self.assertEqual(message["To"], "recipient@example.test")

    def test_exit_email_contains_reason_and_plan_levels(self):
        send_exit_alert("EUR/USD", "BUY", "planned stop-loss touched",
                        1.0890, self.plan)
        message = FakeSMTP.instances[0].messages[0]
        body = message.get_content()
        self.assertIn("TRADE PLAN EXIT: EUR/USD", message["Subject"])
        self.assertIn("planned stop-loss touched", body)
        self.assertIn("Planned stop loss: 1.09000", body)
        self.assertIn("cannot verify that you entered", body)
        self.assertEqual(message["To"], "recipient@example.test")

    def test_exit_email_reports_tp2_reason(self):
        send_exit_alert("XAU/USD", "SELL", "planned TP2 (2R) touched",
                        4100.0, {
                            "action": "SELL", "price": 4120.0,
                            "stop_loss": 4130.0, "take_profit_2": 4100.0,
                        })
        message = FakeSMTP.instances[0].messages[0]
        self.assertIn("planned TP2 (2R) touched", message.get_content())


if __name__ == "__main__":
    unittest.main()
