"""Regression tests for scanner state durability and entry-filter ordering."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main as strategy1
import strategy2
import strategy3


class StrategyStateTests(unittest.TestCase):
    def test_strategy1_state_write_is_atomic_and_valid_json(self):
        self._assert_atomic_writer(strategy1, "save_state")

    def test_strategy2_state_write_is_atomic_and_valid_json(self):
        self._assert_atomic_writer(strategy2, "_save_state")

    def _assert_atomic_writer(self, module, function_name):
        payload = {"XAG/USD": {"key": "test", "active_plan": {"action": "BUY"}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            with patch.object(module, "STATE_PATH", str(path)):
                getattr(module, function_name)(payload)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_strategy2_skips_candle_download_when_entry_session_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = str(Path(tmp) / "strategy2.json")
            with (
                patch.object(strategy2, "STATE_PATH", state_path),
                patch.object(strategy2, "SYMBOLS", ["EUR/USD"]),
                patch.object(strategy2, "london_session_status", return_value=(False, "London session closed")),
                patch.object(strategy2, "fetch_candles") as fetch,
                patch.object(strategy2, "usd_high_impact_news_status") as news,
            ):
                strategy2.main()
            fetch.assert_not_called()
            news.assert_not_called()

    def test_strategy3_btc_is_not_blocked_by_usd_news_calendar(self):
        self.assertFalse(strategy3._requires_usd_news_filter("BTC/USD"))
        self.assertTrue(strategy3._requires_usd_news_filter("XAU/USD"))
        self.assertTrue(strategy3._requires_usd_news_filter("EUR/USD"))
        self.assertTrue(strategy3._requires_usd_news_filter("US500"))


if __name__ == "__main__":
    unittest.main()
