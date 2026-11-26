"""Tests for concurrent market-data warming in the persistent worker."""
import unittest
from unittest.mock import patch

import worker


class WorkerPrefetchTests(unittest.TestCase):
    def test_only_markets_with_both_required_timeframes_are_enabled(self):
        original_strategies = worker.STRATEGIES
        original_intervals = worker.DATA_INTERVALS
        worker.STRATEGIES = (("main.py", "EUR/USD,XAU/USD"),)
        worker.DATA_INTERVALS = ("5min", "1hour")

        def fake_fetch(symbol, interval="5min", **kwargs):
            if symbol == "XAU/USD" and interval == "1hour":
                raise RuntimeError("simulated data outage")
            return object()

        try:
            with patch("worker.fetch_candles", side_effect=fake_fetch) as mocked:
                available = worker.warm_market_data()
            self.assertEqual(available, {"EUR/USD"})
            self.assertEqual(mocked.call_count, 4)
        finally:
            worker.STRATEGIES = original_strategies
            worker.DATA_INTERVALS = original_intervals


if __name__ == "__main__":
    unittest.main()
