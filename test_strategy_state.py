"""Dependency-light regression tests for scanner state and entry-filter behavior.

These tests extract only the functions under test from the scanner source files.
They do not import pandas or execute any strategy calculations.
"""
import ast
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent


def load_functions(filename, names, namespace=None):
    """Compile selected top-level functions without importing module dependencies."""
    source = (ROOT / filename).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=filename)
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    found = {node.name for node in selected}
    missing = set(names) - found
    if missing:
        raise AssertionError(f"Missing functions in {filename}: {sorted(missing)}")
    scope = dict(namespace or {})
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(module, filename, "exec"), scope)
    return scope


class StrategyStateTests(unittest.TestCase):
    def test_strategy1_state_write_is_atomic_and_valid_json(self):
        scope = load_functions(
            "main.py", ["save_state"],
            {"json": json, "os": os, "tempfile": tempfile},
        )
        self._assert_atomic_writer(scope["save_state"], "STATE_PATH")

    def test_strategy2_state_write_is_atomic_and_valid_json(self):
        scope = load_functions(
            "strategy2.py", ["_save_state"],
            {"json": json, "os": os, "tempfile": tempfile},
        )
        self._assert_atomic_writer(scope["_save_state"], "STATE_PATH")

    def _assert_atomic_writer(self, writer, state_name):
        payload = {"XAG/USD": {"key": "test", "active_plan": {"action": "BUY"}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            writer.__globals__[state_name] = str(path)
            writer(payload)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_strategy2_skips_candle_download_when_entry_session_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetch = Mock(name="fetch_candles")
            news = Mock(name="usd_high_impact_news_status")
            scope = load_functions(
                "strategy2.py", ["_save_state", "main"],
                {
                    "json": json, "os": os, "tempfile": tempfile,
                    "datetime": datetime, "timedelta": timedelta,
                    "timezone": timezone, "STATE_PATH": str(Path(tmp) / "state.json"),
                    "select_symbols": lambda requested=None: ["XAU/USD"],
                    "entry_session_status": lambda symbol, now: (False, "London session closed"),
                    "fetch_candles": fetch,
                    "usd_high_impact_news_status": news,
                    "monitor_and_notify": Mock(),
                    "evaluate_strategy2": Mock(),
                    "is_duplicate": Mock(return_value=False),
                    "send_if_not_duplicate": Mock(return_value=False),
                },
            )
            scope["main"]()
            fetch.assert_not_called()
            news.assert_not_called()
            self.assertEqual(json.loads((Path(tmp) / "state.json").read_text()), {})

    def test_strategy3_news_filter_matches_only_approved_markets(self):
        scope = load_functions("strategy3.py", ["_requires_usd_news_filter"])
        requires_filter = scope["_requires_usd_news_filter"]
        self.assertTrue(requires_filter("XAU/USD"))
        self.assertTrue(requires_filter("XAG/USD"))
        self.assertTrue(requires_filter("US500"))
        self.assertFalse(requires_filter("OTHER/PAIR"))


if __name__ == "__main__":
    unittest.main()
