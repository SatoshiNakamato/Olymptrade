import json
import os
from datetime import datetime, timezone, timedelta

from market_data import fetch_candles
from signals import evaluate
from email_alerts import send_alert, send_exit_alert
from market_filters import london_session_status, usd_high_impact_news_status

STATE_PATH = ".signal_state.json"


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _as_utc(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _monitor_plan(symbol, candles, plan, now):
    """Process every completed 5m candle since the last checkpoint, oldest first."""
    closed = candles[candles["datetime"] + timedelta(minutes=5) <= now].copy()
    entry_time = _as_utc(plan["candle_time"])
    checkpoint = _as_utc(plan.get("last_checked_candle", plan["candle_time"]))
    bars = closed[(closed["datetime"] > entry_time) & (closed["datetime"] > checkpoint)]
    is_buy = plan["action"] == "BUY"
    for _, bar in bars.iterrows():
        high, low, close = float(bar.high), float(bar.low), float(bar.close)
        stop = float(plan["stop_loss"])
        target = float(plan["take_profit_2"])
        stop_hit = low <= stop if is_buy else high >= stop
        target_hit = high >= target if is_buy else low <= target
        # If both levels trade inside one OHLC candle, assume stop first.
        if stop_hit:
            return "planned stop-loss touched", close, bar.datetime.isoformat()
        if target_hit:
            return "planned take-profit touched", close, bar.datetime.isoformat()
        plan["last_checked_candle"] = bar.datetime.isoformat()

    created = _as_utc(plan["created_at"])
    if now >= created + timedelta(hours=int(plan["hold_hours"])):
        last_close = float(closed.close.iloc[-1]) if not closed.empty else float(plan["price"])
        return f'{plan["hold_hours"]}-hour review window reached', last_close, now.isoformat()
    return None, None, None


def main():
    symbols = [s.strip() for s in os.getenv("SYMBOLS", "EUR/USD,XAU/USD").split(",") if s.strip()]
    state, now = load_state(), datetime.now(timezone.utc)
    in_session, session_reason = london_session_status(now)
    failures = []

    for symbol in symbols:
        try:
            candles = fetch_candles(symbol)
            previous = state.get(symbol, {})
            plan = previous.get("active_plan")
            if plan:
                reason, price, exit_time = _monitor_plan(symbol, candles, plan, now)
                if reason:
                    send_exit_alert(symbol, plan["action"], reason, price, plan)
                    previous.pop("active_plan", None)
                    previous["last_exit_at"] = exit_time
                    state[symbol] = previous
                    print(f"Exit/review email sent: {symbol}: {reason} at {price}")
                else:
                    previous["active_plan"] = plan
                    state[symbol] = previous
                    print(f"Active plan monitored through {plan.get('last_checked_candle', plan['candle_time'])}")
                # Do not create a second plan while this one is active.
                continue

            # Session/news filters block entries, not monitoring of existing plans.
            if not in_session:
                print(f"{symbol} new entries skipped: {session_reason}")
                continue
            if symbol == "XAU/USD":
                news_ok, news_reason = usd_high_impact_news_status(now)
                if not news_ok:
                    print(f"XAU/USD new entry skipped: {news_reason}")
                    continue

            signal = evaluate(symbol, candles, now)
            print(json.dumps(signal, default=str))
            if signal["action"] == "WAIT":
                continue

            key = f'{symbol}:{signal["action"]}:{signal["candle_time"]}'
            last_time = previous.get("sent_at")
            cooldown_ok = (not last_time or
                           now - _as_utc(last_time) >= timedelta(minutes=20) or
                           previous.get("action") != signal["action"])
            if previous.get("key") == key or not cooldown_ok:
                print(f"Duplicate/cooldown suppressed: {symbol}")
                continue

            send_alert(signal)
            active = {**signal, "created_at": now.isoformat(),
                      "hold_hours": int(signal.get("hold_hours", 5)),
                      "last_checked_candle": signal["candle_time"]}
            state[symbol] = {"key":key, "action":signal["action"],
                             "sent_at":now.isoformat(), "active_plan":active}
            print(f'Email sent: {signal["action"]} {symbol}')
        except Exception as exc:
            failures.append(f"{symbol}: {exc}")
            print(f"ERROR {symbol}: {exc}")

    save_state(state)
    if failures:
        raise RuntimeError("One or more symbols failed: " + " | ".join(failures))


if __name__ == "__main__":
    main()
