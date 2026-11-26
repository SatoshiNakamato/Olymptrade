import unittest

from trade_advisor import recommend_trade


class TradeAdvisorTests(unittest.TestCase):
    def signal(self, symbol="EUR/USD", action="BUY", strategy="s1"):
        base = {
            "symbol": symbol, "action": action, "price": 100.0,
            "entry": 100.0, "stop_loss": 98.0,
            "take_profit_1": 102.0, "take_profit_2": 104.0,
            "take_profit": 104.0, "risk_reward": 2.0,
        }
        if strategy == "s1":
            base.update({"liquidity_sweep": "confirmed", "market_shift": "confirmed",
                         "zone_15m": {"low": 99, "high": 100}})
        elif strategy == "s2":
            base.update({"confirmation_timeframe": "5m",
                         "zone_15m": {"low": 99, "high": 100}})
        else:
            base.update({"trend_1h": "bullish", "trend_15m": "bullish",
                         "pullback_reference_15m_ema20": 100})
        return base

    def test_high_quality_suggests_200_and_100x(self):
        advice = recommend_trade(self.signal())
        self.assertEqual((advice["stake_inr"], advice["multiplier"]), (200, 100))
        self.assertEqual(advice["profit_goal_inr"], 100)

    def test_strategy_two_suggests_100_and_50x(self):
        advice = recommend_trade(self.signal(strategy="s2"))
        self.assertEqual((advice["stake_inr"], advice["multiplier"]), (100, 50))
        self.assertEqual(advice["profit_goal_inr"], 50)

    def test_strategy_three_suggests_100_and_30x(self):
        advice = recommend_trade(self.signal(strategy="s3"))
        self.assertEqual((advice["stake_inr"], advice["multiplier"]), (100, 30))

    def test_us500_uses_user_confirmed_multiplier_choices(self):
        advice = recommend_trade(self.signal(symbol="US500"))
        self.assertEqual(advice["multiplier"], 20)

    def test_sell_profit_estimate_is_positive(self):
        advice = recommend_trade(self.signal(action="SELL"))
        self.assertGreater(advice["gross_at_tp2_inr"], 0)
        self.assertLess(advice["gross_at_stop_inr"], 0)

    def test_other_multiplier_does_not_change_signal_or_block_advice(self):
        signal = self.signal(strategy="s3")
        self.assertEqual(recommend_trade(signal)["multiplier"], 30)
        self.assertEqual(signal["action"], "BUY")


if __name__ == "__main__":
    unittest.main()
