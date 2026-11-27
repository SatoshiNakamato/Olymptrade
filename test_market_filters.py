"""Tests for the three supported markets' entry-session gates."""
import unittest
from datetime import datetime, timezone

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


if __name__ == "__main__":
    unittest.main()
