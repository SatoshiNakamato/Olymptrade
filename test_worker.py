"""Tests for independent strategy processes and market-data prefetch."""
import unittest
from unittest.mock import patch

import worker


class WorkerProcessTests(unittest.TestCase):

    def test_all_strategy_processes_launch_before_any_process_is_waited_on(self):
        original_strategies = worker.STRATEGIES
        worker.STRATEGIES = (
            ("main.py", "EUR/USD"),
            ("strategy2.py", "EUR/USD"),
            ("strategy3.py", "EUR/USD"),
        )
        launched = []

        class FakeProcess:
            def __init__(self, pid):
                self.pid = pid

            def wait(self, timeout=None):
                # This assertion fails if the supervisor waits after launching
                # only one or two strategies instead of launching all three first.
                if len(launched) != 3:
                    raise AssertionError("a strategy was awaited before all three launched")
                return 0

        def fake_popen(args, cwd, env):
            script = args[-1].rsplit("/", 1)[-1]
            launched.append(script)
            return FakeProcess(len(launched) + 1000)

        try:
            with patch("worker.warm_market_data", return_value={"EUR/USD"}), \
                 patch("worker.subprocess.Popen", side_effect=fake_popen):
                worker.run_cycle()
            self.assertCountEqual(
                launched, ["main.py", "strategy2.py", "strategy3.py"]
            )
        finally:
            worker.STRATEGIES = original_strategies
            worker.RUNNING_PROCESSES.clear()

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
