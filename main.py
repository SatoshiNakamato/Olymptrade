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


def main():
    symbols = [s.strip() for s in os.getenv("SYMBOLS", "EUR/USD,XAU/USD").split(",") if s.strip()]
    state, now = load_state(), datetime.now(timezone.utc)

    in_session, session_reason = london_session_status(now)
    failures = []
    for symbol in symbols:
        try:
            candles = fetch_candles(symbol)
            previous = state.get(symbol, {})

            # Monitor an existing theoretical trade plan before looking for a new entry.
            plan = previous.get("active_plan")
            if plan:
                # Ignore the still-forming 5m candle; only closed bars can trigger plan exits.
                closed = candles[candles["datetime"] + timedelta(minutes=5) <= now]
                plan_candle_time = datetime.fromisoformat(plan["candle_time"])
                closed_after_entry = closed[closed["datetime"] > plan_candle_time]
                if closed_after_entry.empty:
                    print(f"Waiting for a completed 5m candle after {symbol} alert")
                    continue
                last = closed_after_entry.iloc[-1]
                high, low, close = float(last.high), float(last.low), float(last.close)
                is_buy = plan["action"] == "BUY"
                stop_hit = low <= float(plan["stop_loss"]) if is_buy else high >= float(plan["stop_loss"])
                target_hit = high >= float(plan["take_profit_2"]) if is_buy else low <= float(plan["take_profit_2"])
                created = datetime.fromisoformat(plan["created_at"])
                hold_expired = now >= created + timedelta(hours=int(plan["hold_hours"]))

                # If stop and target are both inside one 5m candle, assume the stop first (conservative).
                exit_reason = None
                if stop_hit:
                    exit_reason = "planned stop-loss level touched"
                elif target_hit:
                    exit_reason = "planned take-profit level touched"
                elif hold_expired:
                    exit_reason = f'{plan["hold_hours"]}-hour review window reached'

                if exit_reason:
                    send_exit_alert(symbol, plan["action"], exit_reason, close, plan)
                    previous.pop("active_plan", None)
                    previous["last_exit_at"] = now.isoformat()
                    state[symbol] = previous
                    print(f"Exit/review email sent: {symbol}: {exit_reason}")
                else:
                    print(f"Active trade plan monitored: {symbol}")
                # Never issue a fresh entry for the same symbol while a plan is active,
                # or in the same scan that sends its exit notice.
                continue

            # Session and news filters block NEW entries, never exit monitoring.
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
            cooldown_ok = (
                not last_time
                or now - datetime.fromisoformat(last_time) >= timedelta(minutes=20)
                or previous.get("action") != signal["action"]
            )
            if previous.get("key") == key or not cooldown_ok:
                print(f"Duplicate/cooldown suppressed: {symbol}")
                continue

            send_alert(signal)
            state[symbol] = {
                "key": key, "action": signal["action"], "sent_at": now.isoformat(),
                "active_plan": {
                    **signal,
                    "created_at": now.isoformat(),
                    "hold_hours": int(signal.get("hold_hours", 5))
                }
            }
            print(f'Email sent: {signal["action"]} {symbol}')
        except Exception as exc:
            failures.append(f"{symbol}: {exc}")
            print(f"ERROR {symbol}: {exc}")

    save_state(state)
    if failures:
        raise RuntimeError("One or more symbols failed: " + " | ".join(failures))


if __name__ == "__main__":
    main()
