from email_alerts import send_alert

dummy_signal = {
    "symbol": "EUR/USD",
    "action": "BUY",
    "price": 1.10000,
    "entry_zone_low": 1.09980,
    "entry_zone_high": 1.10020,
    "stop_loss": 1.09800,
    "take_profit_1": 1.10200,
    "take_profit_2": 1.10400,
    "risk_reward": 2.0,
    "hold_hours": 5,
    "swing_trend_1h": "BULLISH (TEST)",
    "internal_trend_1h": "BULLISH (TEST)",
    "trend_aligned": True,
    "target_type": "TEST",
    "zone_15m": {
        "side": "DEMAND (TEST)",
        "low": 1.09900,
        "high": 1.10000
    },
    "liquidity_sweep": "TEST ONLY",
    "market_shift": "TEST ONLY",
    "candle_time": "TEST ONLY",
    "valid_until": "TEST ONLY",
    "reason": "Artificial test data. Not a trading recommendation."
}

send_alert(dummy_signal, test_mode=True)
print("SUCCESS: BUY-alert email pipeline test sent.")
