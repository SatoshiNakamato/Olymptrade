"""Tests for the three supported markets' entry-session gates."""
import unittest
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import market_filters
from market_filters import entry_session_status


class EntrySessionStatusTests(unittest.TestCase):
    def test_gold_and_silver_follow_london_session(self):
        opened = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)
        closed = datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)
        self.assertTrue(entry_session_status("XAU/USD", opened)[0])
        self.assertTrue(entry_session_status("XAG/USD", opened)[0])
        self.assertFalse(entry_session_status("XAU/USD", closed)[0])
        self.assertFalse(entry_session_status("XAG/USD", closed)[0])

    def test_us500_follows_new_york_cash_session(self):
        opened = datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc)
        closed = datetime(2026, 11, 27, 21, 0, tzinfo=timezone.utc)
        self.assertTrue(entry_session_status("US500", opened)[0])
        self.assertFalse(entry_session_status("US500", closed)[0])

    def test_unselected_markets_fail_closed(self):
        now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
        for symbol in ("EUR/USD", "GBP/USD", "BTC/USD", "AUD/USD"):
            allowed, reason = entry_session_status(symbol, now)
            self.assertFalse(allowed)
            self.assertIn("Unsupported market", reason)

    def test_weekends_are_closed(self):
        saturday = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
        for symbol in ("XAU/USD", "XAG/USD", "US500"):
            self.assertFalse(entry_session_status(symbol, saturday)[0])


    def test_concurrent_news_checks_share_one_calendar_refresh(self):
        now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
        calls = []

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "calendar.json"
            with patch.object(market_filters, "CALENDAR_CACHE_PATH", cache_path):
                def fetch_once():
                    calls.append("fetch")
                    time.sleep(0.05)
                    market_filters._save_calendar_cache([])
                    return [], "live"

                with patch.object(market_filters, "_fetch_calendar", side_effect=fetch_once):
                    with ThreadPoolExecutor(max_workers=3) as pool:
                        results = list(pool.map(
                            lambda _: market_filters.usd_high_impact_news_status(now),
                            range(3),
                        ))

        self.assertEqual(len(calls), 1)
        self.assertTrue(all(allowed for allowed, _ in results))


if __name__ == "__main__":
    unittest.main()
