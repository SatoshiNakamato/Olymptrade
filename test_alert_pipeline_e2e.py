"""End-to-end BUY/SELL alert -> active-plan -> TP1/exit email tests.

All SMTP traffic is captured in memory. These tests never contact Gmail or a broker.
"""
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from email_alerts import send_alert
from plan_notifications import monitor_and_notify
import strategy2
import strategy3


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.messages = []
        self.login_args = None
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def login(self, sender, password):
        self.login_args = (sender, password)

    def send_message(self, message):
        self.messages.append(message)


class AlertPipelineEndToEndTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances.clear()
        self.env = patch.dict(os.environ, {
            "GMAIL_SENDER": "sender@example.test",
            "GMAIL_APP_PASSWORD": "fake-test-password",
            "ALERT_TO": "recipient@example.test",
        }, clear=True)
        self.env.start()
        self.smtp_patch = patch("email_alerts.smtplib.SMTP_SSL", FakeSMTP)
        self.smtp_patch.start()
        self.now = datetime(2026, 11, 25, 12, 20, tzinfo=timezone.utc)

    def tearDown(self):
        self.smtp_patch.stop()
        self.env.stop()

    def _run_direction(self, action):
        buy = action == "BUY"
        entry = 1.1000
        stop = 1.0900 if buy else 1.1100
        tp1 = 1.1100 if buy else 1.0900
        tp2 = 1.1200 if buy else 1.0800

        signal = {
            "symbol": "EUR/USD", "action": action, "price": entry,
            "entry_zone_low": entry - 0.0010, "entry_zone_high": entry + 0.0010,
            "stop_loss": stop, "take_profit_1": tp1, "take_profit_2": tp2,
            "risk_reward": 2.0, "swing_trend_1h": "bullish" if buy else "bearish",
            "internal_trend_1h": "bullish" if buy else "bearish",
            "trend_aligned": True,
            "zone_15m": {"side": "demand" if buy else "supply",
                         "low": entry - 0.0020, "high": entry + 0.0020},
            "liquidity_sweep": "sell-side" if buy else "buy-side",
            "market_shift": "bullish" if buy else "bearish",
            "candle_time": "2026-11-25T12:00:00+00:00",
            "valid_until": "2026-11-25T12:10:00+00:00",
            "reason": "Synthetic end-to-end test only", "hold_hours": 5,
        }

        # Stage 1: actual BUY/SELL email formatter and SMTP send path.
        send_alert(signal)

        plan = {
            "symbol": "EUR/USD", "action": action, "price": entry,
            "stop_loss": stop, "take_profit_1": tp1, "take_profit_2": tp2,
            "candle_time": signal["candle_time"],
            "last_checked_candle": signal["candle_time"],
            "created_at": "2026-11-25T12:00:00+00:00", "hold_hours": 5,
        }

        # Stage 2: completed 5m candle touches TP1, triggering the real TP1 email.
        if buy:
            first = {"datetime": "2026-11-25T12:05:00Z", "open": 1.100,
                     "high": 1.112, "low": 1.098, "close": 1.108}
            second = {"datetime": "2026-11-25T12:10:00Z", "open": 1.108,
                      "high": 1.122, "low": 1.105, "close": 1.120}
        else:
            first = {"datetime": "2026-11-25T12:05:00Z", "open": 1.100,
                     "high": 1.102, "low": 1.088, "close": 1.092}
            second = {"datetime": "2026-11-25T12:10:00Z", "open": 1.092,
                      "high": 1.095, "low": 1.078, "close": 1.080}

        first_events = monitor_and_notify(
            pd.DataFrame([first]), plan, self.now, "EUR/USD"
        )
        self.assertEqual([event["type"] for event in first_events], ["tp1"])
        self.assertTrue(plan["tp1_notified"])

        # Stage 3: a later completed candle touches TP2, triggering exit email.
        second_events = monitor_and_notify(
            pd.DataFrame([first, second]), plan, self.now, "EUR/USD"
        )
        self.assertEqual([event["type"] for event in second_events], ["exit"])
        self.assertIn("TP2", second_events[0]["reason"])

        messages = [message for smtp in FakeSMTP.instances for message in smtp.messages]
        self.assertEqual(len(messages), 3)
        self.assertIn(f"{action}: EUR/USD", messages[0]["Subject"])
        self.assertIn("TP1 REACHED: EUR/USD", messages[1]["Subject"])
        self.assertIn("TRADE PLAN EXIT: EUR/USD", messages[2]["Subject"])
        self.assertIn("TP1 level touched", messages[1].get_content())
        self.assertIn("planned TP2", messages[2].get_content())
        self.assertTrue(all(message["To"] == "recipient@example.test" for message in messages))
        self.assertEqual(plan["last_checked_candle"], "2026-11-25T12:05:00+00:00")

    def test_buy_entry_tp1_and_tp2_exit_email_pipeline(self):
        self._run_direction("BUY")

    def test_sell_entry_tp1_and_tp2_exit_email_pipeline(self):
        self._run_direction("SELL")


    def test_strategy2_buy_and_sell_entry_email_paths(self):
        base = {
            "strategy": "Strategy 2 test", "symbol": "EUR/USD",
            "direction_15m": "bullish", "zone_15m": {"low": 1.09, "high": 1.10},
            "entry": 1.1000, "entry_zone_low": 1.0990, "entry_zone_high": 1.1010,
            "valid_until": "2026-11-25T12:10:00+00:00",
            "confirmation_timeframe": "5m", "confirmation_level": 1.0990,
            "stop_loss": 1.0900, "take_profit_1": 1.1100, "take_profit": 1.1200,
            "hold_hours": 5, "estimated_profit_inr_proxy": 75.0,
            "candle_time": "2026-11-25T12:00:00+00:00", "reason": "Synthetic test",
        }
        for action in ("BUY", "SELL"):
            signal = dict(base, action=action)
            strategy2._send_email(signal)
        messages = [m for smtp in FakeSMTP.instances for m in smtp.messages]
        self.assertEqual(len(messages), 2)
        self.assertIn("BUY: EUR/USD", messages[0]["Subject"])
        self.assertIn("SELL: EUR/USD", messages[1]["Subject"])
        self.assertIn("Stop-loss:", messages[0].get_content())
        self.assertIn("Take-profit (2R):", messages[1].get_content())

    def test_strategy3_buy_and_sell_entry_email_paths(self):
        base = {
            "strategy": "Strategy 3 test", "symbol": "EUR/USD",
            "entry": 1.1000, "entry_zone_low": 1.0990, "entry_zone_high": 1.1010,
            "stop_loss": 1.0900, "take_profit_1": 1.1100, "take_profit_2": 1.1200,
            "risk_reward": 2.0, "trend_1h": "bullish", "trend_15m": "bullish",
            "rsi_5m": 50.0, "pullback_reference_15m_ema20": 1.1000,
            "hold_hours": 5, "candle_time": "2026-11-25T12:00:00+00:00",
            "valid_until": "2026-11-25T12:10:00+00:00", "reason": "Synthetic test",
        }
        for action in ("BUY", "SELL"):
            signal = dict(base, action=action)
            strategy3._send_email(signal)
        messages = [m for smtp in FakeSMTP.instances for m in smtp.messages]
        self.assertEqual(len(messages), 2)
        self.assertIn("BUY: EUR/USD", messages[0]["Subject"])
        self.assertIn("SELL: EUR/USD", messages[1]["Subject"])
        self.assertIn("TP1 (1R):", messages[0].get_content())
        self.assertIn("TP2 (2R):", messages[1].get_content())


if __name__ == "__main__":
    unittest.main()
