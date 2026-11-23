from indicators import add_indicators

def evaluate(symbol, entry_df, confirm_df, cfg):
    entry = add_indicators(entry_df).iloc[-2]
    confirm = add_indicators(confirm_df).iloc[-2]
    required = ["ema20", "ema50", "rsi", "macd", "macd_signal", "atr"]
    if any(entry[k] != entry[k] for k in required) or any(confirm[k] != confirm[k] for k in required):
        return {"symbol": symbol, "action": "WAIT", "reason": "Not enough clean candle data"}
    price = float(entry["close"])
    atr = float(entry["atr"])
    atr_pct = (atr / price) * 100 if price else 0
    if atr_pct < cfg["min_atr_percent"]:
        return {"symbol": symbol, "action": "WAIT", "reason": "Volatility too low"}
    score, reasons = 0, []
    if entry["ema20"] > entry["ema50"]:
        score += 1; reasons.append("5m EMA20 above EMA50")
    elif entry["ema20"] < entry["ema50"]:
        score -= 1; reasons.append("5m EMA20 below EMA50")
    if confirm["ema20"] > confirm["ema50"]:
        score += 2; reasons.append("15m trend bullish")
    elif confirm["ema20"] < confirm["ema50"]:
        score -= 2; reasons.append("15m trend bearish")
    if entry["macd"] > entry["macd_signal"]:
        score += 1; reasons.append("5m MACD bullish")
    elif entry["macd"] < entry["macd_signal"]:
        score -= 1; reasons.append("5m MACD bearish")
    if 50 < entry["rsi"] < 68:
        score += 1; reasons.append("RSI supports bullish momentum")
    elif 32 < entry["rsi"] < 50:
        score -= 1; reasons.append("RSI supports bearish momentum")
    action = "WAIT"
    if score >= cfg["min_signal_score"] and 50 < entry["rsi"] < 70:
        action = "BUY"
    elif score <= -cfg["min_signal_score"] and 30 < entry["rsi"] < 50:
        action = "SELL"
    stop = price - 1.5*atr if action == "BUY" else price + 1.5*atr
    target = price + 2.0*atr if action == "BUY" else price - 2.0*atr
    return {"symbol":symbol,"action":action,"price":price,
            "stop_loss":stop if action!="WAIT" else None,
            "take_profit":target if action!="WAIT" else None,
            "rsi":float(entry["rsi"]),"score":score,
            "candle_time":str(entry["datetime"]),
            "reason":"; ".join(reasons) if reasons else "No aligned setup"}
