import json
import os
from datetime import datetime, timezone, timedelta

from market_data import fetch_candles
from signals import evaluate
from email_alerts import send_alert, send_exit_alert
from market_filters import london_session_status, usd_high_impact_news_status
from plan_monitor import monitor_plan
from email_alerts import send_tp1_alert

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


def main():
    symbols = [s.strip() for s in os.getenv("SYMBOLS", "EUR/USD,XAU/USD,BTC/USD").split(",") if s.strip()]
    state, now = load_state(), datetime.now(timezone.utc)
    in_session, session_reason = london_session_status(now)
    failures = []

    for symbol in symbols:
        try:
            candles = fetch_candles(symbol)
            previous = state.get(symbol, {})
            plan = previous.get("active_plan")
            if plan:
                events = monitor_plan(candles, plan, now)
                for event in events:
                    if event["type"] == "tp1":
                        send_tp1_alert(symbol, plan["action"], plan["take_profit_1"], plan, event["time"])
                        print(f"TP1 price alert sent: {symbol}")
                    else:
                        send_exit_alert(symbol, plan["action"], event["reason"], event["price"], plan)
                        previous.pop("active_plan", None)
                        previous["last_exit_at"] = event["time"]
                        print(f"Exit/review email sent: {symbol}: {event['reason']} at {event['price']}")
                if "active_plan" in previous:
                    previous["active_plan"] = plan
                state[symbol] = previous
                print(f"Active plan monitored through {plan.get('last_checked_candle', plan['candle_time'])}")
                # Do not create a second plan while this one is active.
                continue

            # Session/news filters block entries, not monitoring of existing plans.
            # FX and gold follow the London-session filter; crypto trades 24/7.
            is_crypto = symbol.upper().startswith(("BTC/", "ETH/"))
            if not is_crypto and not in_session:
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
