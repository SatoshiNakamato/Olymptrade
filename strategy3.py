"""Strategy 3: EUR/USD trend pullback with candle-close confirmation.

This is an alert-only ruleset. It never places broker orders.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from market_data import fetch_candles
from market_filters import london_session_status, usd_high_impact_news_status

STATE_PATH = ".strategy3_state.json"
SYMBOL = "EUR/USD"


def _closed(raw, minutes, now):
    d = raw.set_index("datetime").sort_index()
    bars = d.resample(f"{minutes}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    return bars[bars.index + pd.Timedelta(minutes=minutes) <= now].copy()


def _ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def _atr(df, period=14):
    prev = df.close.shift(1)
    tr = pd.concat(
        [df.high - df.low, (df.high - prev).abs(), (df.low - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def evaluate_strategy3(raw5, now):
    m5 = _closed(raw5, 5, now)
    m15 = _closed(raw5, 15, now)
    h1 = _closed(raw5, 60, now)
    if len(m5) < 80 or len(m15) < 60 or len(h1) < 60:
        return {"symbol": SYMBOL, "action": "WAIT",
                "reason": "Insufficient completed 5m/15m/1h candles"}

    h1_fast, h1_slow = _ema(h1.close, 20), _ema(h1.close, 50)
    m15_fast, m15_slow = _ema(m15.close, 20), _ema(m15.close, 50)
    h1_up = h1_fast.iloc[-1] > h1_slow.iloc[-1] and h1.close.iloc[-1] > h1_fast.iloc[-1]
    h1_down = h1_fast.iloc[-1] < h1_slow.iloc[-1] and h1.close.iloc[-1] < h1_fast.iloc[-1]
    m15_up = m15_fast.iloc[-1] > m15_slow.iloc[-1] and m15.close.iloc[-1] > m15_fast.iloc[-1]
    m15_down = m15_fast.iloc[-1] < m15_slow.iloc[-1] and m15.close.iloc[-1] < m15_fast.iloc[-1]

    if h1_down and m15_down:
        side = "SELL"
    elif h1_up and m15_up:
        side = "BUY"
    else:
        return {"symbol": SYMBOL, "action": "WAIT",
                "reason": "1h and 15m EMA20/EMA50 trend filters are not aligned"}

    # Avoid chasing an extended move. Require a recent 5m pullback to the
    # current 15m EMA20, followed by a completed 5m continuation candle.
    ema20_5 = _ema(m5.close, 20)
    atr = _atr(m5)
    last, prev = m5.iloc[-1], m5.iloc[-2]
    atr_now = float(atr.iloc[-1])
    if pd.isna(atr_now) or atr_now <= 0:
        return {"symbol": SYMBOL, "action": "WAIT", "reason": "ATR unavailable"}

    ema15 = float(m15_fast.iloc[-1])
    recent = m5.iloc[-4:-1]
    touched = bool(((recent.low <= ema15) & (recent.high >= ema15)).any())
    if not touched:
        return {"symbol": SYMBOL, "action": "WAIT",
                "reason": "No recent 5m pullback to the 15m EMA20 reference"}

    # Candle-close confirmation must break the previous completed candle.
    if side == "SELL":
        confirmed = (last.close < last.open and last.close < prev.low
                     and last.close < ema20_5.iloc[-1])
        rsi_delta = m5.close.diff()
        gain = rsi_delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-rsi_delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = float((100 - 100 / (1 + rs)).iloc[-1])
        if not confirmed:
            return {"symbol": SYMBOL, "action": "WAIT",
                    "reason": "Waiting for a bearish 5m candle close below the prior candle low"}
        if not 32 <= rsi <= 58:
            return {"symbol": SYMBOL, "action": "WAIT",
                    "reason": f"5m RSI {rsi:.1f} is outside the sell confirmation band (32–58)"}
        entry = float(last.close)
        swing = float(m5.high.iloc[-7:].max())
        stop = max(swing, entry + 1.2 * atr_now)
        risk = stop - entry
        target1, target2 = entry - risk, entry - 2 * risk
    else:
        confirmed = (last.close > last.open and last.close > prev.high
                     and last.close > ema20_5.iloc[-1])
        rsi_delta = m5.close.diff()
        gain = rsi_delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-rsi_delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = float((100 - 100 / (1 + rs)).iloc[-1])
        if not confirmed:
            return {"symbol": SYMBOL, "action": "WAIT",
                    "reason": "Waiting for a bullish 5m candle close above the prior candle high"}
        if not 42 <= rsi <= 68:
            return {"symbol": SYMBOL, "action": "WAIT",
                    "reason": f"5m RSI {rsi:.1f} is outside the buy confirmation band (42–68)"}
        entry = float(last.close)
        swing = float(m5.low.iloc[-7:].min())
        stop = min(swing, entry - 1.2 * atr_now)
        risk = entry - stop
        target1, target2 = entry + risk, entry + 2 * risk

    if risk <= 0:
        return {"symbol": SYMBOL, "action": "WAIT", "reason": "Invalid stop distance"}

    review_minutes = abs(target2 - entry) / atr_now * 5
    hold_hours = 5 if review_minutes <= 300 else 12 if review_minutes <= 720 else 24
    return {
        "strategy": "Strategy 3 — EUR/USD 1h/15m trend pullback continuation",
        "symbol": SYMBOL, "action": side, "entry": entry,
        "entry_zone_low": min(float(last.low), entry),
        "entry_zone_high": max(float(last.high), entry),
        "stop_loss": stop, "take_profit_1": target1, "take_profit_2": target2,
        "risk_reward": 2.0, "rsi_5m": rsi,
        "trend_1h": "bearish" if h1_down else "bullish",
        "trend_15m": "bearish" if m15_down else "bullish",
        "pullback_reference_15m_ema20": ema15,
        "hold_hours": hold_hours,
        "estimated_review_minutes": round(review_minutes),
        "candle_time": m5.index[-1].isoformat(),
        "valid_until": (m5.index[-1] + pd.Timedelta(minutes=5)).isoformat(),
        "reason": (
            f"1h and 15m trends aligned; recent 5m pullback to 15m EMA20; "
            f"completed {side.lower()} 5m candle broke the prior candle; "
            f"RSI {rsi:.1f}; dynamic ATR/swing stop and 2R target"
        ),
    }


def _send_email(signal):
    import smtplib
    from email.message import EmailMessage

    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("ALERT_TO", "Ineed2pnaga@gmail.com")
    fmt = lambda x: f"{float(x):.5f}"
    msg = EmailMessage()
    msg["Subject"] = f'{signal["action"]}: EUR/USD | Strategy 3 pullback confirmed'
    msg["From"], msg["To"] = sender, recipient
    msg.set_content(
        f'{signal["strategy"]}\n\n'
        f'ACTION: {signal["action"]} EUR/USD\n'
        f'Entry reference: {fmt(signal["entry"])}\n'
        f'5m entry candle range: {fmt(signal["entry_zone_low"])}–{fmt(signal["entry_zone_high"])}\n'
        f'Stop-loss: {fmt(signal["stop_loss"])}\n'
        f'TP1 (1R): {fmt(signal["take_profit_1"])}\n'
        f'TP2 (2R): {fmt(signal["take_profit_2"])}\n'
        f'Risk/reward: 1:{signal["risk_reward"]:.1f}\n'
        f'1h trend: {signal["trend_1h"]}; 15m trend: {signal["trend_15m"]}\n'
        f'5m RSI: {signal["rsi_5m"]:.1f}\n'
        f'15m EMA20 pullback reference: {fmt(signal["pullback_reference_15m_ema20"])}\n'
        f'Estimated review window: {signal["hold_hours"]}h (not a guaranteed holding period)\n'
        f'Confirmed candle (UTC): {signal["candle_time"]}\n'
        f'Entry validity ends (UTC): {signal["valid_until"]}\n\n'
        f'Reason: {signal["reason"]}\n\n'
        'IMPORTANT: This is a conditional alert, not a guaranteed trade. Verify the live broker quote, '
        'spread, and entry validity before acting. Do not enter if price has moved outside the candle range '
        'or the validity time has passed. The bot does not place, monitor, or close broker orders. '
        'Risk sizing and daily loss limits must be enforced manually.'
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)


def main():
    now = datetime.now(timezone.utc)
    in_session, session_reason = london_session_status(now)
    state = {}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    if not in_session:
        print(f"Strategy 3 EUR/USD: WAIT — {session_reason}")
        return
    news_ok, news_reason = usd_high_impact_news_status(now)
    if not news_ok:
        print(f"Strategy 3 EUR/USD: WAIT — {news_reason}")
        return

    raw5 = fetch_candles(SYMBOL, interval="5min")
    signal = evaluate_strategy3(raw5, now)
    print(json.dumps(signal, default=str))
    if signal["action"] == "WAIT":
        return

    key = f'{SYMBOL}:{signal["action"]}:{signal["candle_time"]}'
    old = state.get(SYMBOL, {})
    last_sent = old.get("sent_at")
    cooldown_ok = (not last_sent or
                   now - datetime.fromisoformat(last_sent) >= timedelta(minutes=20)
                   or old.get("action") != signal["action"])
    if old.get("key") == key or not cooldown_ok:
        print("Strategy 3 duplicate/cooldown suppressed: EUR/USD")
        return

    _send_email(signal)
    state[SYMBOL] = {"key": key, "sent_at": now.isoformat(), "action": signal["action"]}
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    print(f'Strategy 3 email sent: {signal["action"]} EUR/USD')


if __name__ == "__main__":
    main()
