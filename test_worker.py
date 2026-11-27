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
    def test_exactly_three_selected_markets_and_all_three_strategies(self):
        self.assertEqual(scanner.MARKETS, ("XAU/USD", "XAG/USD", "US500"))
        self.assertEqual(scanner.STRATEGY_MODULES, ("main", "strategy2", "strategy3"))
        self.assertEqual(scanner.STATE_FILES, (".signal_state.json", ".strategy2_state.json", ".strategy3_state.json"))

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

    def test_three_market_tasks_run_concurrently_and_share_feed(self):
        import sys
        import threading
        from unittest.mock import patch

        active = 0
        maximum_active = 0
        active_lock = threading.Lock()
        barrier = threading.Barrier(3)
        feed_calls = []

        class FakeFrame:
            def copy(self, deep=True):
                return self

        frame = FakeFrame()

        def original_fetch(symbol, outputsize=1600, interval="5min"):
            feed_calls.append((symbol, outputsize, interval))
            return frame

        fake_market_data = SimpleNamespace(fetch_candles=original_fetch)
        task_calls = []

        def fake_task(symbol, shared_fetch):
            nonlocal active, maximum_active
            with active_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            barrier.wait(timeout=3)
            shared_fetch(symbol, interval="5min")
            task_calls.append(symbol)
            with active_lock:
                active -= 1
            return True

        with (
            patch.dict(sys.modules, {"market_data": fake_market_data}),
            patch.object(scanner, "_run_market_task", side_effect=fake_task),
            patch.object(scanner, "_prepare_market_state"),
        ):
            self.assertTrue(scanner.run_cycle())

        self.assertEqual(set(task_calls), set(scanner.MARKETS))
        self.assertEqual(maximum_active, 3)
        self.assertEqual(len(feed_calls), 3)

    def test_dedicated_strategy3_state_is_merged_into_combined_worker_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            symbol = "XAU/USD"
            source_dir = root / ".voroa_strategy3" / "xau_usd"
            source_dir.mkdir(parents=True)
            source = source_dir / ".strategy3_state.json"
            source.write_text(json.dumps({
                symbol: {
                    "key": "dedicated",
                    "sent_at": "2026-11-27T02:00:00+00:00",
                    "active_plan": {"action": "BUY"},
                }
            }), encoding="utf-8")
            target_dir = root / ".voroa_agents" / "xau_usd"
            target_dir.mkdir(parents=True)
            with patch.object(scanner, "ROOT", root), patch.object(scanner, "STATE_ROOT", root / ".voroa_agents"), patch.object(scanner, "log"):
                scanner.migrate_dedicated_strategy3_state()
            target = target_dir / ".strategy3_state.json"
            saved = json.loads(target.read_text(encoding="utf-8"))[symbol]
            self.assertEqual(saved["key"], "dedicated")
            self.assertEqual(saved["active_plan"]["action"], "BUY")
            self.assertTrue(source.exists())

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
            with patch.object(scanner, "ROOT", root), patch.object(scanner, "STATE_ROOT", root / ".voroa_agents"):
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
            with patch.object(scanner, "ROOT", root), patch.object(scanner, "STATE_ROOT", root / ".voroa_agents"):
                scanner.migrate_market_state()
            destination = json.loads((root / ".signal_state.json").read_text())
            self.assertEqual(destination["XAU/USD"]["key"], "central")

    def test_operating_window_includes_strategy3_weekdays_and_existing_sessions(self):
        before_london = datetime.fromisoformat("2026-11-27T07:59:00+00:00")
        london_open = datetime.fromisoformat("2026-11-27T08:00:00+00:00")
        london_closed_ny_open = datetime.fromisoformat("2026-11-27T17:00:00+00:00")
        ny_still_open = datetime.fromisoformat("2026-11-27T20:59:00+00:00")
        both_closed = datetime.fromisoformat("2026-11-27T21:00:00+00:00")
        self.assertTrue(scanner.in_operating_window(before_london))
        self.assertTrue(scanner.in_operating_window(london_open))
        self.assertTrue(scanner.in_operating_window(london_closed_ny_open))
        self.assertTrue(scanner.in_operating_window(ny_still_open))
        self.assertTrue(scanner.in_operating_window(both_closed))

    def test_strategy3_metals_keep_weekday_scanner_awake_outside_london(self):
        friday_early = datetime.fromisoformat("2026-11-27T06:16:00+00:00")
        friday_evening = datetime.fromisoformat("2026-11-27T20:30:00+00:00")
        saturday = datetime.fromisoformat("2026-11-28T12:00:00+00:00")
        self.assertTrue(scanner.strategy3_metals_window_open(friday_early))
        self.assertTrue(scanner.strategy3_metals_window_open(friday_evening))
        self.assertFalse(scanner.strategy3_metals_window_open(saturday))

    def test_next_start_uses_strategy3_weekday_start_and_skips_weekends(self):
        before_london = datetime.fromisoformat("2026-11-27T07:59:00+00:00")
        after_both_close = datetime.fromisoformat("2026-11-27T21:00:00+00:00")
        self.assertEqual(scanner.seconds_until_next_start(before_london), 60)
        self.assertEqual(scanner.seconds_until_next_start(after_both_close), 51 * 3600)

    def test_active_plan_detection_only_checks_selected_markets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent_dir = root / ".voroa_agents" / "xau_usd"
            agent_dir.mkdir(parents=True)
            (agent_dir / ".strategy2_state.json").write_text(json.dumps({
                "XAU/USD": {"active_plan": {"action": "SELL"}}
            }), encoding="utf-8")
            (root / ".signal_state.json").write_text(json.dumps({
                "EUR/USD": {"active_plan": {"action": "BUY"}}
            }), encoding="utf-8")
            with patch.object(scanner, "ROOT", root), patch.object(scanner, "STATE_ROOT", root / ".voroa_agents"):
                self.assertTrue(scanner.has_active_plans())
                (agent_dir / ".strategy2_state.json").write_text(json.dumps({
                    "EUR/USD": {"active_plan": {"action": "BUY"}}
                }), encoding="utf-8")
                self.assertFalse(scanner.has_active_plans())


if __name__ == "__main__":
    unittest.main()
