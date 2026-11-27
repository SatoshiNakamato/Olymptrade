"""Regression tests for the single-process central scanner."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import central_worker as scanner


class CentralScannerTests(unittest.TestCase):
    def test_exactly_three_selected_markets_and_three_strategies(self):
        self.assertEqual(scanner.MARKETS, ("XAU/USD", "XAG/USD", "US500"))
        self.assertEqual(len(scanner.STRATEGY_MODULES), 3)

    def test_shared_feed_downloads_each_symbol_interval_once_per_cycle(self):
        calls = []
        frame = SimpleNamespace(copy=lambda deep=True: "frame-copy")

        def original(symbol, outputsize=1600, interval="5min"):
            calls.append((symbol, outputsize, interval))
            return frame

        fetch, cache, counts = scanner._shared_market_feed(original)
        self.assertEqual(fetch("EUR/USD", interval="5min"), "frame-copy")
        self.assertEqual(fetch("EUR/USD", interval="5min"), "frame-copy")
        self.assertEqual(fetch("EUR/USD", interval="1hour"), "frame-copy")
        self.assertEqual(len(calls), 2)
        self.assertEqual(counts, {"hits": 1, "misses": 2})
        self.assertEqual(len(cache), 2)

    def test_all_three_strategy_mains_run_in_one_process_and_share_feed(self):
        calls = []
        feed_calls = []

        class FakeFrame:
            def copy(self, deep=True):
                return self

        frame = FakeFrame()

        def original_fetch(symbol, outputsize=1600, interval="5min"):
            feed_calls.append((symbol, outputsize, interval))
            return frame

        fake_market_data = SimpleNamespace(fetch_candles=original_fetch)
        fake_modules = {}
        for name in scanner.STRATEGY_MODULES:
            def make_main(strategy_name):
                def run():
                    calls.append(strategy_name)
                    module = fake_modules[strategy_name]
                    module.fetch_candles("EUR/USD", interval="5min")
                return run
            fake_modules[name] = SimpleNamespace(main=make_main(name), fetch_candles=None)

        def importer(name):
            if name == "market_data":
                return fake_market_data
            return fake_modules[name]

        with patch.object(scanner.importlib, "import_module", side_effect=importer):
            self.assertTrue(scanner.run_cycle())

        self.assertEqual(calls, list(scanner.STRATEGY_MODULES))
        self.assertEqual(len(feed_calls), 1)
        self.assertEqual(fake_market_data.fetch_candles, original_fetch)

    def test_previous_per_market_state_is_migrated_without_deleting_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent_dir = root / ".voroa_agents" / "xau_usd"
            agent_dir.mkdir(parents=True)
            source = agent_dir / ".signal_state.json"
            source.write_text(json.dumps({
                "XAU/USD": {
                    "key": "old-agent-key",
                    "sent_at": "2026-11-27T01:00:00+00:00",
                    "active_plan": {"action": "BUY"},
                }
            }), encoding="utf-8")
            with patch.object(scanner, "ROOT", root):
                scanner.migrate_market_state()
            destination = json.loads((root / ".signal_state.json").read_text())
            self.assertEqual(destination["XAU/USD"]["key"], "old-agent-key")
            self.assertEqual(destination["XAU/USD"]["active_plan"]["action"], "BUY")
            self.assertTrue(source.exists())

    def test_existing_newer_central_state_is_preserved_during_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent_dir = root / ".voroa_agents" / "xau_usd"
            agent_dir.mkdir(parents=True)
            (root / ".signal_state.json").write_text(json.dumps({
                "XAU/USD": {"key": "central", "sent_at": "2026-11-27T02:00:00+00:00"}
            }), encoding="utf-8")
            (agent_dir / ".signal_state.json").write_text(json.dumps({
                "XAU/USD": {"key": "older-agent", "sent_at": "2026-11-27T01:00:00+00:00"}
            }), encoding="utf-8")
            with patch.object(scanner, "ROOT", root):
                scanner.migrate_market_state()
            destination = json.loads((root / ".signal_state.json").read_text())
            self.assertEqual(destination["XAU/USD"]["key"], "central")

    def test_operating_window_tracks_london_and_new_york_sessions(self):
        before_london = datetime.fromisoformat("2026-11-27T07:59:00+00:00")
        london_open = datetime.fromisoformat("2026-11-27T08:00:00+00:00")
        london_closed_ny_open = datetime.fromisoformat("2026-11-27T17:00:00+00:00")
        ny_still_open = datetime.fromisoformat("2026-11-27T20:59:00+00:00")
        both_closed = datetime.fromisoformat("2026-11-27T21:00:00+00:00")
        self.assertFalse(scanner.in_operating_window(before_london))
        self.assertTrue(scanner.in_operating_window(london_open))
        self.assertTrue(scanner.in_operating_window(london_closed_ny_open))
        self.assertTrue(scanner.in_operating_window(ny_still_open))
        self.assertFalse(scanner.in_operating_window(both_closed))

    def test_next_start_uses_next_london_or_new_york_open_and_skips_weekends(self):
        before_london = datetime.fromisoformat("2026-11-27T07:59:00+00:00")
        after_both_close = datetime.fromisoformat("2026-11-27T21:00:00+00:00")
        self.assertEqual(scanner.seconds_until_next_start(before_london), 60)
        self.assertEqual(scanner.seconds_until_next_start(after_both_close), 59 * 3600)

    def test_active_plan_detection_only_checks_selected_markets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".signal_state.json").write_text(json.dumps({
                "EUR/USD": {"active_plan": {"action": "BUY"}}
            }), encoding="utf-8")
            with patch.object(scanner, "ROOT", root):
                self.assertFalse(scanner.has_active_plans())
                (root / ".strategy2_state.json").write_text(json.dumps({
                    "XAU/USD": {"active_plan": {"action": "SELL"}}
                }), encoding="utf-8")
                self.assertTrue(scanner.has_active_plans())


if __name__ == "__main__":
    unittest.main()
