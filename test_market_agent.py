"""Tests for per-market parallel strategy execution."""
import unittest
from unittest.mock import patch

import market_agent


class MarketAgentTests(unittest.TestCase):
    def test_all_three_strategy_processes_launch_before_waiting(self):
        original_children = dict(market_agent.CHILDREN)
        market_agent.CHILDREN.clear()
        launched = []

        class FakeProcess:
            def __init__(self, script):
                self.script = script

            def wait(self, timeout=None):
                if len(launched) != 3:
                    raise AssertionError("agent waited before launching all three strategies")
                return 0

        def fake_launch(script):
            launched.append(script)
            return FakeProcess(script)

        try:
            with patch.object(market_agent, "warm_symbol_data"), \
                 patch.object(market_agent, "launch_strategy", side_effect=fake_launch):
                market_agent.run_cycle()
            self.assertCountEqual(
                launched, ["main.py", "strategy2.py", "strategy3.py"]
            )
        finally:
            market_agent.CHILDREN.clear()
            market_agent.CHILDREN.update(original_children)

    def test_each_strategy_receives_only_its_agent_market(self):
        import os
        import tempfile
        from pathlib import Path

        old_root = market_agent.ROOT
        old_agent_symbol = market_agent.SYMBOL
        old_symbol = os.environ.get("VOROA_SYMBOL")
        old_global = os.environ.get("GLOBAL_ALERT_STATE_PATH")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "main.py").write_text("# test", encoding="utf-8")
                market_agent.ROOT = root
                market_agent.SYMBOL = "XAG/USD"
                os.environ["VOROA_SYMBOL"] = "XAG/USD"
                os.environ["GLOBAL_ALERT_STATE_PATH"] = str(root / ".global_alert_state.json")

                class FakeProcess:
                    pid = 123

                with patch("market_agent.subprocess.Popen", return_value=FakeProcess()) as popen:
                    process = market_agent.launch_strategy("main.py")

                self.assertIsNotNone(process)
                env = popen.call_args.kwargs["env"]
                self.assertEqual(env["SYMBOLS"], "XAG/USD")
                self.assertEqual(
                    env["GLOBAL_ALERT_STATE_PATH"],
                    str(root / ".global_alert_state.json"),
                )
        finally:
            market_agent.ROOT = old_root
            market_agent.SYMBOL = old_agent_symbol
            if old_symbol is None:
                os.environ.pop("VOROA_SYMBOL", None)
            else:
                os.environ["VOROA_SYMBOL"] = old_symbol
            if old_global is None:
                os.environ.pop("GLOBAL_ALERT_STATE_PATH", None)
            else:
                os.environ["GLOBAL_ALERT_STATE_PATH"] = old_global


if __name__ == "__main__":
    unittest.main()
