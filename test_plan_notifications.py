"""Tests for reliable retry behavior around plan notification delivery."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from plan_notifications import monitor_and_notify


class PlanNotificationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 25, 12, 20, tzinfo=timezone.utc)
        self.plan = {
            "symbol": "EUR/USD", "action": "BUY", "price": 1.1000,
            "stop_loss": 1.0900, "take_profit_1": 1.1100,
            "take_profit_2": 1.1200,
            "candle_time": "2026-11-25T12:00:00+00:00",
            "last_checked_candle": "2026-11-25T12:00:00+00:00",
            "created_at": "2026-11-25T12:00:00+00:00",
            "hold_hours": 5,
        }
        self.raw = pd.DataFrame([{
            "datetime": "2026-11-25T12:05:00Z",
            "open": 1.100, "high": 1.112, "low": 1.098, "close": 1.108,
        }])

    @patch("plan_notifications.send_tp1_alert")
    def test_successful_tp1_send_keeps_notification_checkpoint(self, send_tp1):
        events = monitor_and_notify(self.raw, self.plan, self.now, "EUR/USD")
        self.assertEqual([e["type"] for e in events], ["tp1"])
        send_tp1.assert_called_once()
        self.assertTrue(self.plan["tp1_notified"])
        self.assertEqual(self.plan["last_checked_candle"], "2026-11-25T12:05:00+00:00")

    @patch("plan_notifications.send_tp1_alert", side_effect=RuntimeError("SMTP unavailable"))
    def test_failed_tp1_send_rolls_back_checkpoint_for_retry(self, send_tp1):
        with self.assertRaisesRegex(RuntimeError, "SMTP unavailable"):
            monitor_and_notify(self.raw, self.plan, self.now, "EUR/USD")
        send_tp1.assert_called_once()
        self.assertNotIn("tp1_notified", self.plan)
        self.assertEqual(self.plan["last_checked_candle"], "2026-11-25T12:00:00+00:00")

    @patch("plan_notifications.send_exit_alert", side_effect=RuntimeError("SMTP unavailable"))
    @patch("plan_notifications.send_tp1_alert")
    def test_exit_retry_does_not_repeat_successful_tp1(self, send_tp1, send_exit):
        both_levels = self.raw.copy()
        both_levels.loc[0, "high"] = 1.122
        with self.assertRaisesRegex(RuntimeError, "SMTP unavailable"):
            monitor_and_notify(both_levels, self.plan, self.now, "EUR/USD")
        send_tp1.assert_called_once()
        send_exit.assert_called_once()
        self.assertTrue(self.plan["tp1_notified"])
        self.assertEqual(self.plan["last_checked_candle"], "2026-11-25T12:00:00+00:00")

        send_exit.reset_mock()
        send_exit.side_effect = None
        events = monitor_and_notify(both_levels, self.plan, self.now, "EUR/USD")
        self.assertEqual([event["type"] for event in events], ["exit"])
        send_tp1.assert_called_once()
        send_exit.assert_called_once()

    @patch("plan_notifications.send_hold_alert")
    def test_hourly_hold_reminder_is_sent_and_checkpointed(self, send_hold):
        later = datetime(2026, 11, 25, 13, 20, tzinfo=timezone.utc)
        events = monitor_and_notify(self.raw, self.plan, later, "EUR/USD")
        self.assertEqual([event["type"] for event in events], ["status"])
        self.assertEqual(events[0]["status"], "HOLD")
        send_hold.assert_called_once()
        self.assertEqual(self.plan["last_status_alert_at"], later.isoformat())

    @patch("plan_notifications.send_hold_alert", side_effect=RuntimeError("SMTP unavailable"))
    def test_failed_hold_reminder_can_retry(self, send_hold):
        later = datetime(2026, 11, 25, 13, 20, tzinfo=timezone.utc)
        with self.assertRaisesRegex(RuntimeError, "SMTP unavailable"):
            monitor_and_notify(self.raw, self.plan, later, "EUR/USD")
        self.assertNotIn("last_status_alert_at", self.plan)

    @patch("plan_notifications.send_exit_alert")
    def test_exit_event_uses_exit_email(self, send_exit):
        stop_raw = self.raw.copy()
        stop_raw.loc[0, "high"] = 1.102
        stop_raw.loc[0, "low"] = 1.088
        events = monitor_and_notify(stop_raw, self.plan, self.now, "EUR/USD")
        self.assertEqual(events[0]["type"], "exit")
        send_exit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
