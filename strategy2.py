import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pandas as pd

from market_data import fetch_candles
from market_filters import london_session_status, new_york_session_status, usd_high_impact_news_status
from plan_notifications import monitor_and_notify
from email_alerts import send_tp1_alert, send_exit_alert
from alert_dedupe import is_duplicate, record_sent, send_if_not_duplicate
from trade_advisor import recommend_trade, format_trade_advice

STATE_PATH = ".strategy2_state.json"
SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "EUR/USD,GBP/USD,USD/JPY,AUD/USD,XAU/USD,XAG/USD,BTC/USD,US500").split(",") if s.strip()]


def _save_state(state):
    """Atomically persist strategy state to survive abrupt worker restarts."""
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=".",
            prefix=".strategy2_state.", suffix=".tmp", delete=False
        ) as handle:
            json.dump(state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, STATE_PATH)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _closed(raw, minutes, now):
    d = raw.set_index("datetime").sort_index()
    bars = d.resample(f"{minutes}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    return bars[bars.index + pd.to_timedelta(minutes, unit="min") <= now]


def _pivots(df, left=2, right=2):
    highs, lows = [], []
    for i in range(left, len(df) - right):
        w = df.iloc[i-left:i+right+1]
        if df.high.iloc[i] == w.high.max():
            highs.append((i, float(df.high.iloc[i])))
        if df.low.iloc[i] == w.low.min():
            lows.append((i, float(df.low.iloc[i])))
    return highs, lows


def _trend(df):
    highs, lows = _pivots(df, 2, 2)
    if len(highs) < 2 or len(lows) < 2:
        return "unclear"
    if highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]:
        return "bullish"
    if highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]:
        return "bearish"
    return "unclear"


def _atr(df, period=14):
    prev = df.close.shift(1)
    tr = pd.concat([df.high-df.low, (df.high-prev).abs(), (df.low-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _zones(m15, direction):
    """Find a compact base followed by a strong candle closing through a confirmed swing."""
    highs, lows = _pivots(m15, 2, 2)
    atr = _atr(m15)
    found = []
    start = max(8, len(m15)-160)
    for i in range(start, len(m15)):
        a = atr.iloc[i]
        if pd.isna(a) or a <= 0:
            continue
        body = abs(float(m15.close.iloc[i] - m15.open.iloc[i]))
        if body < 1.2 * float(a):
            continue
        if direction == "buy":
            prior = next((p for p in reversed(highs) if p[0] + 2 < i and p[0] >= i-24), None)
            if not prior or m15.close.iloc[i] <= prior[1] or m15.close.iloc[i] <= m15.open.iloc[i]:
                continue
            base = [j for j in range(max(0, i-5), i) if abs(m15.close.iloc[j]-m15.open.iloc[j]) <= 0.65*float(a)]
            if not base:
                continue
            base_group = base[-3:]
            lo = float(m15.low.iloc[base_group].min())
            hi = float(m15.high.iloc[base_group].max())
            found.append({"low": lo, "high": hi, "time": m15.index[i].isoformat(), "created": i})
        else:
            prior = next((p for p in reversed(lows) if p[0] + 2 < i and p[0] >= i-24), None)
            if not prior or m15.close.iloc[i] >= prior[1] or m15.close.iloc[i] >= m15.open.iloc[i]:
                continue
            base = [j for j in range(max(0, i-5), i) if abs(m15.close.iloc[j]-m15.open.iloc[j]) <= 0.65*float(a)]
            if not base:
                continue
            base_group = base[-3:]
            lo = float(m15.low.iloc[base_group].min())
            hi = float(m15.high.iloc[base_group].max())
            found.append({"low": lo, "high": hi, "time": m15.index[i].isoformat(), "created": i})
    return found


def _find_mss(df, direction, zone, now):
    """Require a completed candle close through the last opposing pivot after zone touch."""
    zone_ready = pd.Timestamp(zone["time"]) + pd.to_timedelta(15, unit="min")
    touch = [i for i, t in enumerate(df.index)
             if t >= zone_ready and df.low.iloc[i] <= zone["high"] and df.high.iloc[i] >= zone["low"]]
    if not touch:
        return None
    first = touch[-1]
    highs, lows = _pivots(df, 2, 2)
    if direction == "buy":
        levels = [(i, p) for i, p in highs if i < first and p < zone["high"] + 2*float(_atr(df).iloc[-1])]
        if not levels:
            return None
        level = levels[-1][1]
        candidates = [i for i in range(first+1, len(df)) if df.close.iloc[i] > level]
        if not candidates:
            return None
        ci = candidates[-1]
        swing_lows = [(i, p) for i, p in lows if first <= i <= ci]
        if not swing_lows:
            return None
        stop = min(p for _, p in swing_lows)
    else:
        levels = [(i, p) for i, p in lows if i < first and p > zone["low"] - 2*float(_atr(df).iloc[-1])]
        if not levels:
            return None
        level = levels[-1][1]
        candidates = [i for i in range(first+1, len(df)) if df.close.iloc[i] < level]
        if not candidates:
            return None
        ci = candidates[-1]
        swing_highs = [(i, p) for i, p in highs if first <= i <= ci]
        if not swing_highs:
            return None
        stop = max(p for _, p in swing_highs)
    return {"level": float(level), "close": float(df.close.iloc[ci]), "stop": float(stop),
            "confirmation_time": df.index[ci].isoformat(), "distance": abs(float(level)-zone["high" if direction=="buy" else "low"])}


def evaluate_strategy2(symbol, raw5, raw1, now):
    m15 = _closed(raw5, 15, now)
    m5 = _closed(raw5, 5, now)
    m1 = _closed(raw1, 1, now) if raw1 is not None else pd.DataFrame()
    if len(m15) < 40 or len(m5) < 80:
        return {"symbol": symbol, "action": "WAIT", "reason": "Insufficient completed 15m/5m candles"}
    direction = _trend(m15)
    if direction == "unclear":
        return {"symbol": symbol, "action": "WAIT", "reason": "15m market structure is unclear"}
    zones = _zones(m15, direction)
    if not zones:
        return {"symbol": symbol, "action": "WAIT", "reason": f"No valid 15m {direction} demand/supply base"}
    side = "buy" if direction == "bullish" else "sell"
    zone = zones[-1]
    last15 = m15.iloc[-1]
    if not (last15.low <= zone["high"] and last15.high >= zone["low"]):
        return {"symbol": symbol, "action": "WAIT", "reason": "Price has not returned to the latest 15m zone"}
    confirm = _find_mss(m5, side, zone, now)
    confirmation_tf = "5m"
    # If the 5m confirmation pivot is materially distant from the zone, use 1m structure.
    if confirm and confirm["distance"] > 1.5 * float(_atr(m5).iloc[-1]):
        if m1.empty:
            return {"symbol": symbol, "action": "WAIT", "reason": "5m confirmation level too far from zone; request 1m fallback"}
        confirm = _find_mss(m1, side, zone, now)
        confirmation_tf = "1m"
    if not confirm:
        return {"symbol": symbol, "action": "WAIT", "reason": "Zone touched; waiting for candle-close market-structure shift"}
    entry = float(confirm["close"])
    stop = float(confirm["stop"])
    risk = abs(entry-stop)
    if risk <= 0:
        return {"symbol": symbol, "action": "WAIT", "reason": "Invalid swing stop distance"}
    target1 = entry + risk if side == "buy" else entry - risk
    target = entry + 2*risk if side == "buy" else entry - 2*risk
    atr = float(_atr(m5).iloc[-1])
    if pd.isna(atr) or atr <= 0:
        return {"symbol": symbol, "action": "WAIT", "reason": "Unable to estimate review window"}
    mins = abs(target-entry)/atr*5
    hours = 5 if mins <= 300 else 12 if mins <= 720 else 24
    confirm_time = pd.Timestamp(confirm["confirmation_time"])
    confirm_frame = m1 if confirmation_tf == "1m" else m5
    if confirm_time not in confirm_frame.index:
        return {"symbol": symbol, "action": "WAIT", "reason": "Confirmation candle unavailable for entry-zone calculation"}
    confirm_bar = confirm_frame.loc[confirm_time]
    entry_low, entry_high = float(confirm_bar.low), float(confirm_bar.high)
    valid_until = (m5.index[-1] + pd.Timedelta(minutes=10)).isoformat()
    return {
        "strategy": "Strategy 2 — 15m trend, zone and lower-timeframe MSS",
        "symbol": symbol, "action": "BUY" if side == "buy" else "SELL",
        "direction_15m": direction, "zone_15m": zone,
        "entry": entry, "entry_zone_low": min(entry_low, entry_high), "entry_zone_high": max(entry_low, entry_high),
        "valid_until": valid_until, "take_profit_1": target1, "stop_loss": stop, "take_profit": target,
        "risk_distance": risk, "risk_reward": 2.0,
        "confirmation_timeframe": confirmation_tf,
        "confirmation_level": confirm["level"],
        "confirmation_time": confirm["confirmation_time"],
        "hold_hours": hours, "estimated_review_minutes": round(mins),
        "candle_time": m5.index[-1].isoformat(),
        "reason": "15m trend aligned; price returned to the 15m origin base; lower-timeframe candle closed beyond the last opposing pivot"
    }


def _send_email(signal):
    import smtplib
    from email.message import EmailMessage
    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("ALERT_TO", "Ineed2pnaga@gmail.com")
    symbol = signal["symbol"]
    advice = recommend_trade(signal)
    signal["trade_advice"] = advice
    digits = 3 if symbol == "USD/JPY" else 5 if symbol in {"EUR/USD", "GBP/USD", "AUD/USD"} else 2 if symbol in {"BTC/USD", "US500"} else 3
    fmt = lambda x: f"{float(x):.{digits}f}"
    msg = EmailMessage()
    msg["Subject"] = f'{signal["action"]}: {symbol} | Strategy 2 confirmed'
    msg["From"], msg["To"] = sender, recipient
    msg.set_content(
        f'{signal["strategy"]}\n\n'
        f'{signal["action"]} {symbol}\n'
        f'15m direction: {signal["direction_15m"]}\n'
        f'15m zone: {fmt(signal["zone_15m"]["low"])} - {fmt(signal["zone_15m"]["high"])}\n'
        f'Entry reference (confirmation close): {fmt(signal["entry"])}\n'
        f'Entry zone: {fmt(signal["entry_zone_low"])} - {fmt(signal["entry_zone_high"])}\n'
        f'Entry valid until (UTC): {signal["valid_until"]}\n'
        f'Confirmation: {signal["confirmation_timeframe"]} candle closed beyond {fmt(signal["confirmation_level"])}\n'
        f'Stop-loss: {fmt(signal["stop_loss"])}\n'
        f'TP1 (1R): {fmt(signal["take_profit_1"])}\n'
        f'Take-profit (2R): {fmt(signal["take_profit"])}\n'
        f'Risk/reward: 1:2\n'
        f'Estimated review window: {signal["hold_hours"]} hours (not a guaranteed holding time)\n'
        f'Confirmed candle time: {signal["candle_time"]} UTC\n\n'
        f'Reason: {signal["reason"]}\n\n'
        + format_trade_advice(advice, fmt) + '\n\n'
        'These are optional estimates, not guaranteed broker P/L. Verify the live quote, spread and entry validity. '
        'The bot cannot see your fill, actual multiplier, open position or realized P/L. It never places or closes orders.'
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)


def main():
    now = datetime.now(timezone.utc)
    in_session, why = london_session_status(now)
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        state = {}
    errors = []
    for symbol in SYMBOLS:
        try:
            old = state.get(symbol, {})
            active = old.get("active_plan")
            if active:
                # Active plans must keep being monitored even outside entry sessions/news windows.
                raw5 = fetch_candles(symbol, interval="5min")
                events = monitor_and_notify(raw5, active, now, symbol)
                for event in events:
                    if event["type"] == "tp1":
                        print("Strategy 2 TP1 price alert sent: " + symbol)
                    elif event["type"] == "profit_goal":
                        print("Strategy 2 suggested rupee objective price reached: " + symbol)
                    elif event["type"] == "status":
                        print("Strategy 2 " + event["status"] + " reminder sent: " + symbol)
                    else:
                        old.pop("active_plan", None)
                        old["last_exit_at"] = event["time"]
                        print("Strategy 2 exit/review alert sent: " + symbol + ": " + event["reason"])
                if "active_plan" in old:
                    old["active_plan"] = active
                state[symbol] = old
                continue

            # Apply entry gates before downloading candles or running pandas analysis.
            # FX/metals follow London hours; US500 follows New York hours; crypto is 24/7.
            is_crypto = symbol.upper().startswith(("BTC/", "ETH/"))
            if symbol == "US500":
                market_open, market_reason = new_york_session_status(now)
            else:
                market_open, market_reason = in_session, why
            if not is_crypto and not market_open:
                print(f"Strategy 2 {symbol}: WAIT — {market_reason}")
                continue
            if symbol in {"EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "XAU/USD", "XAG/USD", "US500"}:
                ok, news_reason = usd_high_impact_news_status(now)
                if not ok:
                    print(f"Strategy 2 {symbol}: WAIT — {news_reason}")
                    continue

            raw5 = fetch_candles(symbol, interval="5min")
            # Request 1m candles only when the 5m structure specifically needs fallback.
            signal = evaluate_strategy2(symbol, raw5, None, now)
            if signal["action"] == "WAIT" and (
                "market-structure shift" in signal.get("reason", "")
                or "1m fallback" in signal.get("reason", "")
            ):
                raw1 = fetch_candles(symbol, interval="1min")
                signal = evaluate_strategy2(symbol, raw5, raw1, now)
            print(json.dumps(signal, default=str))
            if signal["action"] == "WAIT":
                continue
            detected_at = datetime.now(timezone.utc)
            print(
                f"[{detected_at.isoformat(timespec='milliseconds')}] "
                f"Strategy 2 SIGNAL_DETECTED {symbol} {signal['action']} "
                f"confirmation={signal.get('confirmation_time')} "
                f"timeframe={signal.get('confirmation_timeframe')}",
                flush=True,
            )
            key = f'{symbol}:{signal["action"]}:{signal["candle_time"]}:{signal["confirmation_timeframe"]}'
            old = state.get(symbol, {})
            last = old.get("sent_at")
            cooldown = not last or now - datetime.fromisoformat(last) >= timedelta(minutes=20)
            if is_duplicate(symbol, signal["action"], now):
                print(f"Strategy 2 cross-strategy duplicate suppressed: {symbol}")
                continue
            if old.get("key") == key or not cooldown:
                print(f"Strategy 2 duplicate/cooldown suppressed: {symbol}")
                continue
            sent = send_if_not_duplicate(
                symbol, signal["action"], now, lambda: _send_email(signal),
                strategy="Strategy 2",
            )
            if not sent:
                print(f"Strategy 2 duplicate suppressed at send lock: {symbol}")
                continue
            active = {"symbol": symbol, "action": signal["action"], "price": signal["entry"],
                      "stop_loss": signal["stop_loss"], "take_profit_1": signal["take_profit_1"],
                      "take_profit_2": signal["take_profit"], "candle_time": signal["candle_time"],
                      "created_at": now.isoformat(), "hold_hours": signal["hold_hours"],
                      "last_checked_candle": signal["candle_time"],
                      "trade_advice": signal.get("trade_advice")}
            state[symbol] = {"key": key, "sent_at": now.isoformat(),
                             "action": signal["action"], "active_plan": active}
            print(f'Strategy 2 email sent: {signal["action"]} {symbol}')
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
            print(f"Strategy 2 ERROR {symbol}: {exc}")
    _save_state(state)
    if errors:
        raise RuntimeError("Strategy 2 scan failures: " + " | ".join(errors))


if __name__ == "__main__":
    main()
