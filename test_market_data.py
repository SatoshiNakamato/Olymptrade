"""Market-data integrity tests for stale and future candle rejection."""
import unittest

import pandas as pd

from market_data import MarketDataError, _validate_freshness


class MarketDataFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.now = pd.Timestamp("2026-11-27T12:10:00Z")

    def frame(self, opened_at):
        return pd.DataFrame([{
            "datetime": pd.Timestamp(opened_at),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
        }])

    def test_fresh_closed_five_minute_candle_is_accepted(self):
        _validate_freshness(self.frame("2026-11-27T12:05:00Z"), "5min", self.now)

    def test_stale_five_minute_candle_is_rejected(self):
        with self.assertRaisesRegex(MarketDataError, "stale"):
            _validate_freshness(self.frame("2026-11-27T11:50:00Z"), "5min", self.now)

    def test_stale_one_minute_fallback_is_rejected(self):
        with self.assertRaisesRegex(MarketDataError, "stale"):
            _validate_freshness(self.frame("2026-11-27T12:06:00Z"), "1min", self.now)

    def test_stale_hourly_candle_is_rejected(self):
        with self.assertRaisesRegex(MarketDataError, "stale"):
            _validate_freshness(self.frame("2026-11-27T08:00:00Z"), "1hour", self.now)

    def test_future_dated_candle_is_rejected(self):
        with self.assertRaisesRegex(MarketDataError, "future-dated"):
            _validate_freshness(self.frame("2026-11-27T12:15:00Z"), "5min", self.now)


if __name__ == "__main__":
    unittest.main()
