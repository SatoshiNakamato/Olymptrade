import pandas as pd
from indicators import add_indicators

def _resample(df, minutes, now):
    indexed = df.set_index("datetime").sort_index()
    rule = f"{minutes}min"
    bars = indexed.resample(rule, label="left", closed="left").agg({
        "open":"first", "high":"max", "low":"min", "close":"last"
    }).dropna()
    # Keep only candles whose full interval has ended.
    return bars[bars.index + pd.Timedelta(minutes=minutes) <= now]

def _last_indicated(df):
    if len(df) < 55:
        return None, None
    enriched = add_indicators(df)
    row = enriched.iloc[-1]
    needed = ["ema20","ema50","rsi","macd","macd_signal","atr"]
    if any(pd.isna(row[k]) for k in needed):
        return None, None
    return row, enriched

def evaluate(symbol, raw_5m, now):
    frames = {m: _resample(raw_5m, m, now) for m in (5,15,60,240)}
    rows = {}
    for m, frame in frames.items():
        row, enriched = _last_indicated(frame)
        if row is None:
            return {"symbol":symbol,"action":"WAIT","reason":f"Insufficient closed {m}m candles"}
        rows[m] = (row, enriched)

    e5, hist5 = rows[5]
    e15, _ = rows[15]
    e1h, _ = rows[60]
    e4h, _ = rows[240]
    price, atr = float(e5["close"]), float(e5["atr"])
    if price <= 0 or atr <= 0:
        return {"symbol":symbol,"action":"WAIT","reason":"Invalid price or ATR"}

    # A fresh 5m candle must break the prior candle's high/low in the direction
    # of the trade, while 5m/15m/1h trends agree.
    prior = hist5.iloc[-2]
    bullish = (e5["ema20"] > e5["ema50"] and e15["ema20"] > e15["ema50"]
               and e1h["ema20"] > e1h["ema50"])
    bearish = (e5["ema20"] < e5["ema50"] and e15["ema20"] < e15["ema50"]
               and e1h["ema20"] < e1h["ema50"])
    breakout_up = price > float(prior["high"]) and float(e5["close"]) > float(e5["open"])
    breakout_down = price < float(prior["low"]) and float(e5["close"]) < float(e5["open"])
    macd_up, macd_down = e5["macd"] > e5["macd_signal"], e5["macd"] < e5["macd_signal"]
    rsi = float(e5["rsi"])
    action, reasons = "WAIT", []
    if bullish and breakout_up and macd_up and 52 <= rsi <= 68:
        action = "BUY NOW"
        reasons = ["5m, 15m and 1h trends aligned bullish","closed 5m candle broke prior high",
                   "MACD bullish","RSI in bullish momentum zone"]
    elif bearish and breakout_down and macd_down and 32 <= rsi <= 48:
        action = "SELL NOW"
        reasons = ["5m, 15m and 1h trends aligned bearish","closed 5m candle broke prior low",
                   "MACD bearish","RSI in bearish momentum zone"]

    direction = 1 if action == "BUY NOW" else -1
    risk = 1.5 * atr
    stop = price - direction * risk
    tp1 = price + direction * risk
    tp2 = price + direction * (2.0 * risk)
    four_hour_aligned = (e4h["ema20"] > e4h["ema50"]) if direction == 1 else (e4h["ema20"] < e4h["ema50"])
    hold_window = "2–4 hours (4h trend agrees)" if four_hour_aligned else "1–2 hours maximum; 4h trend does not confirm"
    return {
        "symbol":symbol,"action":action,"price":price,
        "stop_loss":stop if action != "WAIT" else None,
        "take_profit_1":tp1 if action != "WAIT" else None,
        "take_profit_2":tp2 if action != "WAIT" else None,
        "risk_distance":risk,"rsi":rsi,
        "hold_window":hold_window if action != "WAIT" else "No trade",
        "four_hour_trend":"aligned" if four_hour_aligned else "not aligned",
        "candle_time":str(e5.name if hasattr(e5,"name") else frames[5].index[-1]),
        "valid_until":"next 5-minute candle close",
        "reason":"; ".join(reasons) if reasons else "No confirmed multi-timeframe breakout",
    }
