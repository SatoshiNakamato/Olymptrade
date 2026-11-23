import pandas as pd

# Price-action rules are deliberately explicit and conservative.
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
SWEEP_LOOKBACK = 12
ZONE_LOOKBACK = 24
MIN_RR = 2.0


def _resample(df, minutes, now):
    indexed = df.set_index("datetime").sort_index()
    bars = indexed.resample(f"{minutes}min", label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last"
    }).dropna()
    return bars[bars.index + pd.Timedelta(minutes=minutes) <= now]


def _pivots(df, left=PIVOT_LEFT, right=PIVOT_RIGHT):
    highs, lows = [], []
    for i in range(left, len(df) - right):
        h = float(df["high"].iloc[i])
        l = float(df["low"].iloc[i])
        if h >= float(df["high"].iloc[i-left:i+right+1].max()):
            highs.append((i, h))
        if l <= float(df["low"].iloc[i-left:i+right+1].min()):
            lows.append((i, l))
    return highs, lows


def _structure(df):
    highs, lows = _pivots(df)
    if len(highs) < 2 or len(lows) < 2:
        return "unclear", highs, lows
    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    ll = lows[-1][1] < lows[-2][1]
    if hh and hl:
        return "bullish", highs, lows
    if lh and ll:
        return "bearish", highs, lows
    return "unclear", highs, lows


def _zones_15m(df):
    """Find recent order-block-like zones only when displacement closes through structure."""
    highs, lows = _pivots(df)
    zones = []
    start = max(5, len(df) - 80)
    for i in range(start, len(df)):
        row = df.iloc[i]
        body = abs(float(row.close) - float(row.open))
        prior = df.iloc[max(0, i-12):i]
        if len(prior) < 5:
            continue
        prior_high, prior_low = float(prior.high.max()), float(prior.low.min())
        atr = float((prior.high - prior.low).rolling(10).mean().iloc[-1])
        if pd.isna(atr) or atr <= 0 or body < 1.2 * atr:
            continue
        if float(row.close) > prior_high and row.close > row.open:
            candidates = [j for j in range(max(0, i-5), i) if df.iloc[j].close < df.iloc[j].open]
            if candidates:
                j = candidates[-1]
                zones.append({"side":"demand","low":float(df.low.iloc[j]),
                              "high":float(df.open.iloc[j]),"time":str(df.index[j]),
                              "created":i})
        if float(row.close) < prior_low and row.close < row.open:
            candidates = [j for j in range(max(0, i-5), i) if df.iloc[j].close > df.iloc[j].open]
            if candidates:
                j = candidates[-1]
                zones.append({"side":"supply","low":float(df.open.iloc[j]),
                              "high":float(df.high.iloc[j]),"time":str(df.index[j]),
                              "created":i})
    return zones


def _wait(symbol, reason, **extra):
    result = {"symbol":symbol, "action":"WAIT", "reason":reason}
    result.update(extra)
    return result


def evaluate(symbol, raw_5m, now):
    frames = {m: _resample(raw_5m, m, now) for m in (5, 15, 60)}
    m5, m15, h1 = frames[5], frames[15], frames[60]
    if len(m5) < 80 or len(m15) < 40 or len(h1) < 30:
        return _wait(symbol, "Insufficient closed candles for structure analysis")

    swing, h1_highs, h1_lows = _structure(h1)
    if swing == "unclear" or len(h1_highs) < 2 or len(h1_lows) < 2:
        return _wait(symbol, "1h swing structure is unclear")
    internal, _, _ = _structure(m5)
    if internal == "unclear":
        return _wait(symbol, "5m internal structure is unclear")

    # The latest confirmed 1h swing high/low defines the dealing range midpoint.
    range_high, range_low = h1_highs[-1][1], h1_lows[-1][1]
    if range_high <= range_low:
        return _wait(symbol, "Invalid 1h dealing range")
    midpoint = (range_high + range_low) / 2.0
    price = float(m5.close.iloc[-1])
    zones = _zones_15m(m15)
    if not zones:
        return _wait(symbol, "No valid 15m supply/demand zone formed by displacement and structure break")

    # Only use a zone created by a completed 15m candle, still unmitigated, and on the correct side of midpoint.
    latest15 = len(m15) - 1
    valid = []
    for z in zones:
        if z["created"] >= latest15:
            continue
        touched_after = m15.iloc[z["created"]+1:]
        if z["side"] == "supply":
            mitigated = (touched_after.close > z["high"]).any()
            if z["low"] > midpoint and not mitigated:
                valid.append(z)
        else:
            mitigated = (touched_after.close < z["low"]).any()
            if z["high"] < midpoint and not mitigated:
                valid.append(z)
    if not valid:
        return _wait(symbol, "Price has no fresh 15m zone in the correct premium/discount half")

    # Align the 1h swing trend with the intended trade. Countertrend internal moves are rejected.
    sells_allowed = swing == "bearish" and internal == "bearish"
    buys_allowed = swing == "bullish" and internal == "bullish"
    if not (sells_allowed or buys_allowed):
        return _wait(symbol, "1h swing and 1h internal structure do not align")

    direction = "sell" if sells_allowed else "buy"
    matching = [z for z in valid if z["side"] == ("supply" if direction == "sell" else "demand")]
    if not matching:
        return _wait(symbol, f"No valid 15m {direction} zone in premium/discount")
    zone = matching[-1]
    recent = m5.iloc[-(SWEEP_LOOKBACK+1):-1]
    last = m5.iloc[-1]
    prev = m5.iloc[-2]
    recent_high, recent_low = float(recent.high.max()), float(recent.low.min())
    highs5, lows5 = _pivots(m5.iloc[:-1])
    if not highs5 or not lows5:
        return _wait(symbol, "No confirmed 5m internal swing to validate liquidity sweep and market shift")

    # Require the sweep, a later candle-close MSS, then a pullback into the new 5m origin zone.
    scan_start = max(10, len(m5) - 18)
    setup = None
    for sweep_i in range(scan_start, len(m5)-2):
        sweep = m5.iloc[sweep_i]
        after = m5.iloc[sweep_i+1:]
        if direction == "sell":
            prior_high = float(m5.high.iloc[max(0, sweep_i-SWEEP_LOOKBACK):sweep_i].max())
            if float(sweep.high) <= prior_high or float(sweep.close) >= prior_high:
                continue
            internal_lows = [(idx, val) for idx, val in lows5 if idx < sweep_i]
            if not internal_lows:
                continue
            target_low = internal_lows[-1][1]
            shifts = [k for k in range(sweep_i+1, len(m5)) if float(m5.close.iloc[k]) < target_low]
            if not shifts:
                continue
            shift_i = shifts[0]
            origins = [j for j in range(max(sweep_i, shift_i-5), shift_i) if m5.close.iloc[j] > m5.open.iloc[j]]
            if not origins:
                continue
            origin = origins[-1]
            zlow, zhigh = float(m5.open.iloc[origin]), float(m5.high.iloc[origin])
            pullback = m5.iloc[shift_i+1:]
            touched = any(float(r.high) >= zlow and float(r.low) <= zhigh for _, r in pullback.iterrows())
            if touched and float(last.close) <= zhigh and float(last.high) >= zlow:
                setup = {"sweep":sweep_i,"shift":shift_i,"zone_low":zlow,"zone_high":zhigh,
                         "sweep_extreme":float(sweep.high)}
                break
        else:
            prior_low = float(m5.low.iloc[max(0, sweep_i-SWEEP_LOOKBACK):sweep_i].min())
            if float(sweep.low) >= prior_low or float(sweep.close) <= prior_low:
                continue
            internal_highs = [(idx, val) for idx, val in highs5 if idx < sweep_i]
            if not internal_highs:
                continue
            target_high = internal_highs[-1][1]
            shifts = [k for k in range(sweep_i+1, len(m5)) if float(m5.close.iloc[k]) > target_high]
            if not shifts:
                continue
            shift_i = shifts[0]
            origins = [j for j in range(max(sweep_i, shift_i-5), shift_i) if m5.close.iloc[j] < m5.open.iloc[j]]
            if not origins:
                continue
            origin = origins[-1]
            zlow, zhigh = float(m5.low.iloc[origin]), float(m5.open.iloc[origin])
            pullback = m5.iloc[shift_i+1:]
            touched = any(float(r.low) <= zhigh and float(r.high) >= zlow for _, r in pullback.iterrows())
            if touched and float(last.high) >= zlow and float(last.low) <= zhigh:
                setup = {"sweep":sweep_i,"shift":shift_i,"zone_low":zlow,"zone_high":zhigh,
                         "sweep_extreme":float(sweep.low)}
                break

    if setup is None:
        return _wait(symbol, "Missing required 5m liquidity sweep → candle-close market shift → pullback sequence")
    if direction == "sell" and float(last.close) > zone["high"]:
        return _wait(symbol, "Price broke above the 15m supply zone; setup invalidated")
    if direction == "buy" and float(last.close) < zone["low"]:
        return _wait(symbol, "Price broke below the 15m demand zone; setup invalidated")

    entry = (setup["zone_low"] + setup["zone_high"]) / 2.0
    stop = max(setup["sweep_extreme"], setup["zone_high"]) if direction == "sell" else min(setup["sweep_extreme"], setup["zone_low"])
    risk = abs(stop - entry)
    if risk <= 0:
        return _wait(symbol, "Invalid stop distance")
    target = entry - MIN_RR*risk if direction == "sell" else entry + MIN_RR*risk
    # Do not claim a valid 2R if the next opposing 5m swing blocks the path.
    opposing = [v for _, v in (highs5 if direction == "buy" else lows5) if
                (v > entry if direction == "buy" else v < entry)]
    if opposing:
        next_zone = min(opposing) if direction == "buy" else max(opposing)
        available = abs(next_zone-entry)
        if available < MIN_RR*risk:
            return _wait(symbol, "Next opposing 5m liquidity is too close; less than 1:2 reward-to-risk")
        target = next_zone

    action = "BUY NOW" if direction == "buy" else "SELL NOW"
    return {
        "symbol":symbol, "action":action, "price":entry, "entry_zone_low":setup["zone_low"],
        "entry_zone_high":setup["zone_high"], "stop_loss":stop,
        "take_profit_1":entry + (1 if direction=="buy" else -1)*risk,
        "take_profit_2":target, "risk_distance":risk, "risk_reward":round(abs(target-entry)/risk,2),
        "swing_trend_1h":swing, "internal_trend_5m":internal,
        "zone_15m":zone, "liquidity_sweep":"confirmed", "market_shift":"5m candle close confirmed",
        "hold_window":"Manage by price action; no fixed holding-time promise",
        "candle_time":str(m5.index[-1]), "valid_until":"next 5-minute candle close",
        "reason":"1h swing and 1h internal structure aligned; fresh 15m zone in premium/discount; 5m liquidity sweep, close-confirmed MSS and pullback entry; minimum 1:2 R:R."
    }
