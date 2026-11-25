"""Tests for completed-candle trade-plan monitoring."""
import unittest
from datetime import datetime, timezone

import pandas as pd

from plan_monitor import monitor_plan


class PlanMonitorTests(unittest.TestCase):
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

    def bars(self, rows):
        return pd.DataFrame([
            {"datetime": f"2026-11-25T12:{minute:02d}:00Z",
             "open": op, "high": high, "low": low, "close": close}
            for minute, op, high, low, close in rows
        ])

    def test_tp1_emits_once_and_advances_checkpoint(self):
        raw = self.bars([(5, 1.100, 1.112, 1.098, 1.108),
                         (10, 1.108, 1.109, 1.105, 1.107)])
        events = monitor_plan(raw, self.plan, self.now)
        self.assertEqual([event["type"] for event in events], ["tp1"])
        self.assertTrue(self.plan["tp1_notified"])
        self.assertEqual(self.plan["last_checked_candle"], "2026-11-25T12:10:00+00:00")
        self.assertEqual(monitor_plan(raw, self.plan, self.now), [])

    def test_stop_loss_emits_exit_for_buy(self):
        raw = self.bars([(5, 1.100, 1.102, 1.088, 1.091)])
        events = monitor_plan(raw, self.plan, self.now)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "exit")
        self.assertIn("stop-loss", events[0]["reason"])

    def test_tp2_emits_exit_for_buy(self):
        raw = self.bars([(5, 1.110, 1.122, 1.108, 1.120)])
        events = monitor_plan(raw, self.plan, self.now)
        self.assertEqual([event["type"] for event in events], ["tp1", "exit"])
        self.assertIn("TP2", events[-1]["reason"])

    def test_sell_tp1_and_stop_levels_are_reversed(self):
        self.plan.update({
            "action": "SELL", "price": 1.1000,
            "stop_loss": 1.1100, "take_profit_1": 1.0900,
            "take_profit_2": 1.0800,
        })
        raw = self.bars([(5, 1.100, 1.102, 1.088, 1.091)])
        events = monitor_plan(raw, self.plan, self.now)
        self.assertEqual([event["type"] for event in events], ["tp1"])

        sell_plan = dict(self.plan)
        sell_plan["last_checked_candle"] = "2026-11-25T12:00:00+00:00"
        raw_stop = self.bars([(5, 1.100, 1.112, 1.098, 1.109)])
        exit_events = monitor_plan(raw_stop, sell_plan, self.now)
        self.assertEqual(exit_events[0]["type"], "exit")
        self.assertIn("stop-loss", exit_events[0]["reason"])

    def test_unclosed_five_minute_candle_is_ignored(self):
        raw = self.bars([(5, 1.100, 1.115, 1.095, 1.110)])
        before_close = datetime(2026, 11, 25, 12, 9, 59, tzinfo=timezone.utc)
        self.assertEqual(monitor_plan(raw, self.plan, before_close), [])
        self.assertFalse(self.plan.get("tp1_notified", False))


if __name__ == "__main__":
    unittest.main()
