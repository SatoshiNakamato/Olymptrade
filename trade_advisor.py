"""Rule-based stake and multiplier suggestions for alert emails.

All P/L figures are rough gross estimates, not broker quotes or guarantees.
This module uses only the Python standard library (no pandas dependency).
"""

def _entry(signal):
    return float(signal.get("price", signal.get("entry")))

def _target2(signal):
    return float(signal.get("take_profit_2", signal.get("take_profit")))

def _pnl_at(entry, level, stake, multiplier, action):
    direction = 1.0 if str(action).upper() == "BUY" else -1.0
    return stake * multiplier * ((float(level) - entry) / entry) * direction

def recommend_trade(signal):
    """Return a suggestion for a signal that has already passed strategy rules.

    Scores describe rule evidence only; they are not win probabilities.
    """
    entry = _entry(signal)
    stop = float(signal["stop_loss"])
    tp1 = float(signal["take_profit_1"])
    tp2 = _target2(signal)
    action = str(signal["action"]).upper()
    symbol = str(signal["symbol"]).upper()
    rr = float(signal.get("risk_reward", 2.0))

    if signal.get("liquidity_sweep") and signal.get("market_shift") and signal.get("zone_15m"):
        score, tier = 92, "HIGH"
    elif signal.get("confirmation_timeframe") and signal.get("zone_15m"):
        score, tier = 86, "STRONG"
    elif signal.get("trend_1h") and signal.get("trend_15m") and signal.get("pullback_reference_15m_ema20") is not None:
        score, tier = 82, "GOOD"
    else:
        score, tier = 78, "QUALIFIED"

    if rr >= 2.5:
        score = min(99, score + 3)
    if action not in {"BUY", "SELL"} or entry <= 0:
        raise ValueError("A BUY/SELL signal with a positive entry price is required")

    stake = 200 if score >= 90 else 100
    if symbol == "US500":
        multiplier = 20 if score >= 90 else 15 if score >= 85 else 10
    else:
        multiplier = 100 if score >= 90 else 50 if score >= 85 else 30

    profit_goal = stake * 0.5
    direction = 1.0 if action == "BUY" else -1.0
    target_price = entry * (1.0 + direction * profit_goal / (stake * multiplier))
    target_before_tp2 = (
        target_price <= tp2 if action == "BUY" else target_price >= tp2
    )

    return {
        "quality_score": score,
        "quality_tier": tier,
        "stake_inr": stake,
        "multiplier": multiplier,
        "profit_goal_inr": profit_goal,
        "target_price": target_price,
        "target_before_tp2": target_before_tp2,
        "gross_at_tp1_inr": _pnl_at(entry, tp1, stake, multiplier, action),
        "gross_at_tp2_inr": _pnl_at(entry, tp2, stake, multiplier, action),
        "gross_at_stop_inr": _pnl_at(entry, stop, stake, multiplier, action),
    }

def format_trade_advice(advice, price_formatter):
    target_note = (
        "The rupee target price is before TP2."
        if advice["target_before_tp2"]
        else "TP2 may be reached before this rupee target; respect the strategy TP2."
    )
    return (
        "POSITION SUGGESTION (optional; you choose the actual order):\n"
        f"Rule-based setup quality: {advice['quality_tier']} "
        f"({advice['quality_score']}/100; NOT a win probability)\n"
        f"Suggested investment: ₹{advice['stake_inr']}\n"
        f"Suggested multiplier: {advice['multiplier']}x "
        "(suggestion only; use any multiplier actually available to you)\n"
        f"Suggested gross profit objective: +₹{advice['profit_goal_inr']:.0f}\n"
        f"Approx. price for that objective at the suggested settings: "
        f"{price_formatter(advice['target_price'])}\n"
        f"Estimated gross P/L at TP1: ₹{advice['gross_at_tp1_inr']:.2f}; "
        f"at TP2: ₹{advice['gross_at_tp2_inr']:.2f}; "
        f"at stop: ₹{advice['gross_at_stop_inr']:.2f}\n"
        f"{target_note}\n"
        "Estimates exclude broker commission, spread, conversion and execution differences. "
        "If you change the stake or multiplier, the rupee estimates and target price change. "
        "At your rupee objective or the strategy TP2 (whichever comes first), check broker P/L "
        "and close the full position manually if that is your plan."
    )
