"""Regression tests for cross-strategy alert deduplication."""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import alert_dedupe


class AlertDedupeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = alert_dedupe.STATE_PATH
        alert_dedupe.STATE_PATH = Path(self.temp_dir.name) / ".global_alert_state.json"
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        alert_dedupe.STATE_PATH = self.original_path
        self.temp_dir.cleanup()

    def test_same_symbol_and_direction_is_suppressed_within_cooldown(self):
        alert_dedupe.record_sent("EUR/USD", "BUY", self.now)
        self.assertTrue(alert_dedupe.is_duplicate(
            "EUR/USD", "BUY", self.now + timedelta(minutes=5)
        ))

    def test_same_direction_is_allowed_after_cooldown(self):
        alert_dedupe.record_sent("EUR/USD", "BUY", self.now)
        self.assertFalse(alert_dedupe.is_duplicate(
            "EUR/USD", "BUY", self.now + timedelta(minutes=21)
        ))

    def test_opposite_direction_is_not_blocked_by_same_direction_dedupe(self):
        alert_dedupe.record_sent("EUR/USD", "BUY", self.now)
        self.assertFalse(alert_dedupe.is_duplicate(
            "EUR/USD", "SELL", self.now + timedelta(minutes=1)
        ))

    def test_different_symbol_is_not_blocked(self):
        alert_dedupe.record_sent("EUR/USD", "BUY", self.now)
        self.assertFalse(alert_dedupe.is_duplicate(
            "XAU/USD", "BUY", self.now + timedelta(minutes=1)
        ))

    def test_concurrent_workers_send_only_one_same_direction_alert(self):
        sent = []

        def fake_send():
            sent.append("sent")

        def attempt(_):
            return alert_dedupe.send_if_not_duplicate(
                "EUR/USD", "BUY", self.now, fake_send
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))

        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(sent, ["sent"])
        self.assertTrue(alert_dedupe.is_duplicate(
            "EUR/USD", "BUY", datetime.now(timezone.utc)
        ))

    def test_different_alerts_can_send_in_parallel(self):
        barrier = Barrier(2)
        sent = []

        def fake_send(symbol):
            barrier.wait(timeout=3)
            sent.append(symbol)

        def attempt(symbol):
            return alert_dedupe.send_if_not_duplicate(
                symbol, "BUY", self.now, lambda: fake_send(symbol)
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ["EUR/USD", "XAU/USD"]))

        self.assertEqual(results, [True, True])
        self.assertCountEqual(sent, ["EUR/USD", "XAU/USD"])

    def test_state_is_valid_json_after_atomic_write(self):
        alert_dedupe.record_sent("XAU/USD", "SELL", self.now)
        self.assertEqual(
            alert_dedupe.STATE_PATH.read_text(encoding="utf-8").strip()[0], "{"
        )
        self.assertTrue(alert_dedupe.is_duplicate(
            "XAU/USD", "SELL", self.now + timedelta(minutes=1)
        ))


if __name__ == "__main__":
    unittest.main()
