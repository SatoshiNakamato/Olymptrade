"""Strategy 3: three-market trend pullback continuation alerts.

Scans only XAU/USD, XAG/USD, and US500.
Alert-only: never places broker orders.
"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pandas as pd

from market_data import fetch_candles
from market_filters import strategy3_entry_session_status, usd_high_impact_news_status
from plan_notifications import monitor_and_notify
from email_alerts import send_tp1_alert, send_exit_alert
from alert_dedupe import is_duplicate, record_sent, send_if_not_duplicate
from trade_advisor import recommend_trade, format_trade_advice
from watchlist import select_symbols

STATE_PATH = ".strategy3_state.json"


def _save_state(state):
    """Atomically persist state, including an empty first-run state file."""
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=".", prefix=".strategy3_state.", suffix=".tmp", delete=False) as handle:
            json.dump(state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, STATE_PATH)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)



def _requires_usd_news_filter(symbol):
    """Return whether this market is gated by the USD high-impact event calendar."""
    return symbol in {"XAU/USD", "XAG/USD", "US500"}


def _closed(raw, minutes, now):
    d = raw.set_index("datetime").sort_index()
    bars = d.resample(f"{minutes}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    return bars[bars.index + pd.to_timedelta(minutes, unit="min") <= now].copy()


def _ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def _atr(df, period=14):
    prev = df.close.shift(1)
    tr = pd.concat(
        [df.high - df.low, (df.high - prev).abs(), (df.low - prev).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period).mean()


def _rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def evaluate_strategy3(raw5, now, symbol, raw1h=None):
    m5 = _closed(raw5, 5, now)
    m15 = _closed(raw5, 15, now)
    h1 = _closed(raw1h if raw1h is not None else raw5, 60, now)
    if len(m5) < 80 or len(m15) < 50 or len(h1) < 50:
        return {"symbol": symbol, "action": "WAIT",
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
        return {"symbol": symbol, "action": "WAIT",
                "reason": "1h and 15m EMA20/EMA50 trends are not aligned"}

    ema20_5 = _ema(m5.close, 20)
    atr = _atr(m5)
    last, prev = m5.iloc[-1], m5.iloc[-2]
    atr_now = float(atr.iloc[-1])
    if pd.isna(atr_now) or atr_now <= 0:
        return {"symbol": symbol, "action": "WAIT", "reason": "ATR unavailable"}

    ema15 = float(m15_fast.iloc[-1])
    recent = m5.iloc[-4:-1]
    touched = bool(((recent.low <= ema15) & (recent.high >= ema15)).any())
    if not touched:
        return {"symbol": symbol, "action": "WAIT",
                "reason": "No recent 5m pullback to 15m EMA20"}

    rsi = float(_rsi(m5.close).iloc[-1])
    if side == "SELL":
        confirmed = (last.close < last.open and last.close < prev.low
                     and last.close < ema20_5.iloc[-1])
        if not confirmed:
            return {"symbol": symbol, "action": "WAIT",
                    "reason": "Waiting for bearish 5m close below previous candle low"}
        if not 32 <= rsi <= 58:
            return {"symbol": symbol, "action": "WAIT",
                    "reason": f"5m RSI {rsi:.1f} outside sell band (32–58)"}
        entry = float(last.close)
        stop = max(float(m5.high.iloc[-7:].max()), entry + 1.2 * atr_now)
        risk = stop - entry
        target1, target2 = entry - risk, entry - 2 * risk
    else:
        confirmed = (last.close > last.open and last.close > prev.high
                     and last.close > ema20_5.iloc[-1])
        if not confirmed:
            return {"symbol": symbol, "action": "WAIT",
                    "reason": "Waiting for bullish 5m close above previous candle high"}
        if not 42 <= rsi <= 68:
            return {"symbol": symbol, "action": "WAIT",
                    "reason": f"5m RSI {rsi:.1f} outside buy band (42–68)"}
        entry = float(last.close)
        stop = min(float(m5.low.iloc[-7:].min()), entry - 1.2 * atr_now)
        risk = entry - stop
        target1, target2 = entry + risk, entry + 2 * risk

    if risk <= 0:
        return {"symbol": symbol, "action": "WAIT", "reason": "Invalid stop distance"}

    review_minutes = abs(target2 - entry) / atr_now * 5
    hold_hours = 5 if review_minutes <= 300 else 12 if review_minutes <= 720 else 24
    return {
        "strategy": "Strategy 3 — multi-market 1h/15m trend pullback",
        "symbol": symbol, "action": side, "entry": entry,
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
        "valid_until": (m5.index[-1] + pd.Timedelta(minutes=10)).isoformat(),
        "reason": (f"1h/15m trends aligned; recent 5m EMA20 pullback; completed "
                   f"{side.lower()} candle broke previous candle; RSI {rsi:.1f}; "
                   "ATR/swing stop and 2R target"),
    }


def _fmt(symbol, value):
    digits = 2 if symbol == "US500" else 3
    return f"{float(value):.{digits}f}"


def _send_email(signal):
    import smtplib
    from email.message import EmailMessage

    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("ALERT_TO", "Ineed2pnaga@gmail.com")
    symbol = signal["symbol"]
    advice = recommend_trade(signal)
    signal["trade_advice"] = advice
    fmt = lambda x: _fmt(symbol, x)
    msg = EmailMessage()
    msg["Subject"] = f'{signal["action"]}: {symbol} | Strategy 3 confirmed'
    msg["From"], msg["To"] = sender, recipient
    msg.set_content(
        f'{signal["strategy"]}\n\n'
        f'ACTION: {signal["action"]} {symbol}\n'
        f'Entry reference: {fmt(signal["entry"])}\n'
        f'5m entry candle range: {fmt(signal["entry_zone_low"])}–{fmt(signal["entry_zone_high"])}\n'
        f'Stop-loss: {fmt(signal["stop_loss"])}\n'
        f'TP1 (1R): {fmt(signal["take_profit_1"])}\n'
        f'TP2 (2R): {fmt(signal["take_profit_2"])}\n'
        f'Risk/reward: 1:{signal["risk_reward"]:.1f}\n'
        f'1h trend: {signal["trend_1h"]}; 15m trend: {signal["trend_15m"]}\n'
        f'5m RSI: {signal["rsi_5m"]:.1f}\n'
        f'15m EMA20 pullback reference: {fmt(signal["pullback_reference_15m_ema20"])}\n'
        f'Estimated review window: {signal["hold_hours"]}h (not guaranteed)\n'
        f'Confirmed candle (UTC): {signal["candle_time"]}\n'
        f'Entry validity ends (UTC): {signal["valid_until"]}\n\n'
        f'Reason: {signal["reason"]}\n\n'
        + format_trade_advice(advice, fmt) + '\n\n'
        'This is an alert, not a guaranteed trade. Verify broker quote, spread and entry validity. '
        'The bot cannot see your fill, actual multiplier, open position or realized P/L. '
        'It monitors theoretical chart levels only and never places, changes or closes orders.'
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)


def main(symbols=None):
    symbols = select_symbols(symbols if symbols is not None else os.getenv("SYMBOLS"))
    if not symbols:
        print("No approved markets selected; Strategy 3 cycle skipped.", flush=True)
        return
    state = {}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        state = {}
    # Create a cacheable state file even when all markets return WAIT.
    _save_state(state)

    scan_errors = []
    for symbol in symbols:
        now = datetime.now(timezone.utc)
        old = state.get(symbol, {})
        active = old.get("active_plan")
        try:
            # Do not fetch or analyze a market that is outside its entry session.
            # Existing plans remain eligible for monitoring outside entry sessions.
            if not active:
                in_session, reason = strategy3_entry_session_status(symbol, now)
                if not in_session:
                    print(f"Strategy 3 {symbol}: WAIT — {reason}")
                    continue
                if _requires_usd_news_filter(symbol):
                    news_ok, news_reason = usd_high_impact_news_status(now)
                    if not news_ok:
                        print(f"Strategy 3 {symbol}: WAIT — {news_reason}")
                        continue

            raw5 = fetch_candles(symbol, interval="5min")
            if active:
                events = monitor_and_notify(raw5, active, now, symbol)
                for event in events:
                    if event["type"] == "tp1":
                        print("Strategy 3 TP1 price alert sent: " + symbol)
                    elif event["type"] == "profit_goal":
                        print("Strategy 3 suggested rupee objective price reached: " + symbol)
                    elif event["type"] == "status":
                        print("Strategy 3 " + event["status"] + " reminder sent: " + symbol)
                    else:
                        old.pop("active_plan", None)
                        old["last_exit_at"] = event["time"]
                        print("Strategy 3 exit/review alert sent: " + symbol + ": " + event["reason"])
                if "active_plan" in old:
                    old["active_plan"] = active
                state[symbol] = old
                _save_state(state)
                continue

            raw1h = fetch_candles(symbol, interval="1hour")
            signal = evaluate_strategy3(raw5, now, symbol, raw1h=raw1h)
        except Exception as exc:
            scan_errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
            print(f"Strategy 3 {symbol}: scan error — {exc}")
            continue

        print(json.dumps(signal, default=str))
        if signal["action"] == "WAIT":
            continue

        detected_at = datetime.now(timezone.utc)
        in_session, reason = strategy3_entry_session_status(symbol, detected_at)
        if not in_session:
            print(f"Strategy 3 {symbol}: entry suppressed at send time — {reason}")
            continue
        news_ok, news_reason = usd_high_impact_news_status(detected_at)
        if not news_ok:
            print(f"Strategy 3 {symbol}: entry suppressed at send time — {news_reason}")
            continue
        now = detected_at
        print(
            f"[{detected_at.isoformat(timespec='milliseconds')}] "
            f"Strategy 3 SIGNAL_DETECTED {symbol} {signal['action']} "
            f"confirmation_candle={signal.get('candle_time')}",
            flush=True,
        )
        key = f'{symbol}:{signal["action"]}:{signal["candle_time"]}'
        old = state.get(symbol, {})
        last_sent = old.get("sent_at")
        cooldown_ok = (not last_sent or
                       now - datetime.fromisoformat(last_sent) >= timedelta(minutes=20)
                       or old.get("action") != signal["action"])
        if is_duplicate(symbol, signal["action"], now):
            print(f"Strategy 3 cross-strategy duplicate suppressed: {symbol}")
            continue
        if old.get("key") == key or not cooldown_ok:
            print(f"Strategy 3 duplicate/cooldown suppressed: {symbol}")
            continue

        try:
            sent = send_if_not_duplicate(
                symbol, signal["action"], now, lambda: _send_email(signal),
                strategy="Strategy 3",
            )
            if not sent:
                print(f"Strategy 3 duplicate suppressed at send lock: {symbol}")
                continue
            active = {"symbol": symbol, "action": signal["action"], "price": signal["entry"],
                      "stop_loss": signal["stop_loss"], "take_profit_1": signal["take_profit_1"],
                      "take_profit_2": signal["take_profit_2"], "candle_time": signal["candle_time"],
                      "created_at": now.isoformat(), "hold_hours": signal["hold_hours"],
                      "last_checked_candle": signal["candle_time"],
                      "trade_advice": signal.get("trade_advice")}
            state[symbol] = {"key": key, "sent_at": now.isoformat(),
                             "action": signal["action"], "active_plan": active}
            _save_state(state)
            print(f'Strategy 3 email sent: {signal["action"]} {symbol}')
        except Exception as exc:
            scan_errors.append(f"{symbol}: email delivery failed: {type(exc).__name__}")
            print(f"Strategy 3 {symbol}: email error — {exc}")

    _save_state(state)
    if scan_errors:
        raise RuntimeError("Strategy 3 scan failures: " + " | ".join(scan_errors))


if __name__ == "__main__":
    main()
