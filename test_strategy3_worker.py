"""Tests for the dedicated Strategy 3 worker split."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import strategy3_worker as worker
import central_worker as scanner


class Strategy3WorkerTests(unittest.TestCase):
    def test_worker_configuration_is_strategy3_only(self):
        old = (scanner.STRATEGY_MODULES, scanner.STATE_FILES, scanner.STATE_ROOT, scanner.WORKER_NAME)
        try:
            worker.configure_worker()
            self.assertEqual(scanner.STRATEGY_MODULES, ("strategy3",))
            self.assertEqual(scanner.STATE_FILES, (".strategy3_state.json",))
            self.assertEqual(scanner.STATE_ROOT, scanner.ROOT / ".voroa_strategy3")
            self.assertEqual(scanner.WORKER_NAME, "STRATEGY3_SCANNER")
            self.assertEqual(scanner.MARKETS, ("XAU/USD", "XAG/USD", "US500"))
        finally:
            scanner.STRATEGY_MODULES, scanner.STATE_FILES, scanner.STATE_ROOT, scanner.WORKER_NAME = old

    def test_imports_legacy_active_plan_without_overwriting_existing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_root = scanner.ROOT
            old_state_root = scanner.STATE_ROOT
            try:
                scanner.ROOT = root
                scanner.STATE_ROOT = root / ".voroa_strategy3"
                symbol = "XAU/USD"
                legacy = root / ".voroa_agents" / "xau_usd"
                legacy.mkdir(parents=True)
                (legacy / ".strategy3_state.json").write_text(json.dumps({
                    symbol: {"active_plan": {"action": "BUY"}, "key": "legacy"}
                }), encoding="utf-8")
                with patch.object(scanner, "log"):
                    worker.import_legacy_strategy3_state()
                target = scanner._market_state_dir(symbol) / ".strategy3_state.json"
                self.assertEqual(
                    json.loads(target.read_text(encoding="utf-8"))[symbol]["active_plan"]["action"],
                    "BUY",
                )
            finally:
                scanner.ROOT = old_root
                scanner.STATE_ROOT = old_state_root

    def test_import_does_not_replace_existing_strategy3_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_root = scanner.ROOT
            old_state_root = scanner.STATE_ROOT
            try:
                scanner.ROOT = root
                scanner.STATE_ROOT = root / ".voroa_strategy3"
                symbol = "XAU/USD"
                target = scanner._market_state_dir(symbol) / ".strategy3_state.json"
                scanner._atomic_json(target, {symbol: {"key": "new", "active_plan": {"action": "SELL"}}})
                legacy = root / ".voroa_agents" / "xau_usd"
                legacy.mkdir(parents=True)
                (legacy / ".strategy3_state.json").write_text(json.dumps({
                    symbol: {"key": "old", "active_plan": {"action": "BUY"}}
                }), encoding="utf-8")
                worker.import_legacy_strategy3_state()
                self.assertEqual(json.loads(target.read_text())[symbol]["active_plan"]["action"], "SELL")
            finally:
                scanner.ROOT = old_root
                scanner.STATE_ROOT = old_state_root


if __name__ == "__main__":
    unittest.main()
