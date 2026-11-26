"""Tests for eight independent Voroa market agents and safe state migration."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import worker


class WorkerMarketAgentTests(unittest.TestCase):
    def test_exactly_eight_markets_are_configured(self):
        self.assertEqual(len(worker.MARKETS), 8)
        self.assertEqual(
            set(worker.MARKETS),
            {"EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
             "XAU/USD", "XAG/USD", "BTC/USD", "US500"},
        )

    def test_all_market_agents_are_launched_without_serial_waits(self):
        started = []

        class FakeProcess:
            pass

        def fake_launch(symbol):
            started.append(symbol)
            return FakeProcess()

        with patch.object(worker, "launch_agent", side_effect=fake_launch):
            launched = worker.launch_all_agents()

        self.assertEqual(set(started), set(worker.MARKETS))
        self.assertEqual(set(launched), set(worker.MARKETS))
        self.assertEqual(len(started), 8)

    def test_old_shared_state_is_migrated_per_market_without_overwriting_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            agents = root / ".voroa_agents"
            root.mkdir()
            (root / ".signal_state.json").write_text(json.dumps({
                "EUR/USD": {"key": "eur-key", "active_plan": {"action": "BUY"}},
                "BTC/USD": {"key": "btc-key"},
            }), encoding="utf-8")
            (root / ".strategy2_state.json").write_text(json.dumps({
                "EUR/USD": {"key": "s2-eur"},
            }), encoding="utf-8")
            (root / ".strategy3_state.json").write_text(json.dumps({
                "BTC/USD": {"key": "s3-btc"},
            }), encoding="utf-8")

            with patch.object(worker, "ROOT", root), patch.object(worker, "AGENT_DIR", agents):
                eur_dir = worker.prepare_agent_state("EUR/USD")
                btc_dir = worker.prepare_agent_state("BTC/USD")

            eur = json.loads((eur_dir / ".signal_state.json").read_text(encoding="utf-8"))
            btc = json.loads((btc_dir / ".signal_state.json").read_text(encoding="utf-8"))
            self.assertEqual(eur, {"EUR/USD": {"key": "eur-key", "active_plan": {"action": "BUY"}}})
            self.assertEqual(btc, {"BTC/USD": {"key": "btc-key"}})
            self.assertTrue((eur_dir / ".strategy2_state.json").exists())
            self.assertTrue((btc_dir / ".strategy3_state.json").exists())

    def test_startup_email_test_remains_explicitly_opt_in(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "send_test_email.py").write_text("# test", encoding="utf-8")
            with (
                patch.object(worker, "ROOT", root),
                patch.dict(os.environ, {"SEND_TEST_EMAIL_ON_START": "true"}),
                patch("worker.subprocess.run") as run,
            ):
                worker.run_startup_email_test()
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][-1], str(root / "send_test_email.py"))

    def test_existing_agent_state_is_never_overwritten_by_legacy_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            agents = root / ".voroa_agents"
            root.mkdir()
            (root / ".signal_state.json").write_text(
                json.dumps({"EUR/USD": {"key": "legacy"}}), encoding="utf-8"
            )
            state_dir = agents / "eur_usd"
            state_dir.mkdir(parents=True)
            target = state_dir / ".signal_state.json"
            target.write_text(json.dumps({"EUR/USD": {"key": "current"}}), encoding="utf-8")

            with patch.object(worker, "ROOT", root), patch.object(worker, "AGENT_DIR", agents):
                worker.prepare_agent_state("EUR/USD")

            saved = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(saved["EUR/USD"]["key"], "current")


if __name__ == "__main__":
    unittest.main()
