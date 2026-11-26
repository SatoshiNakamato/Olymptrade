"""Tests for concurrent market-data warming in the persistent worker."""
import unittest
from threading import Barrier
from unittest.mock import patch

import worker


class WorkerPrefetchTests(unittest.TestCase):

    def test_three_strategy_workers_start_without_waiting_for_each_other(self):
        original_strategies = worker.STRATEGIES
        worker.STRATEGIES = (
            ("main.py", "EUR/USD"),
            ("strategy2.py", "EUR/USD"),
            ("strategy3.py", "EUR/USD"),
        )
        start_barrier = Barrier(3)
        started = []

        def fake_run_strategy(script, symbols):
            start_barrier.wait(timeout=5)
            started.append(script)
            return 0

        try:
            with patch("worker.warm_market_data", return_value={"EUR/USD"}), \
                 patch("worker.run_strategy", side_effect=fake_run_strategy):
                worker.run_cycle()
            self.assertCountEqual(started, ["main.py", "strategy2.py", "strategy3.py"])
        finally:
            worker.STRATEGIES = original_strategies

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
