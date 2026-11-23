import json
import os
from datetime import datetime, timezone, timedelta

from market_data import fetch_candles
from signals import evaluate
from email_alerts import send_alert
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
    if not in_session:
        print(f"Market scan skipped: {session_reason}")
        return

    failures = []
    for symbol in symbols:
        try:
            if symbol == "XAU/USD":
                news_ok, news_reason = usd_high_impact_news_status(now)
                if not news_ok:
                    print(f"XAU/USD skipped: {news_reason}")
                    continue

            candles = fetch_candles(symbol)
            signal = evaluate(symbol, candles, now)
            print(json.dumps(signal, default=str))
            if signal["action"] == "WAIT":
                continue

            key = f'{symbol}:{signal["action"]}:{signal["candle_time"]}'
            previous = state.get(symbol, {})
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
            state[symbol] = {"key": key, "action": signal["action"], "sent_at": now.isoformat()}
            print(f'Email sent: {signal["action"]} {symbol}')
        except Exception as exc:
            failures.append(f"{symbol}: {exc}")
            print(f"ERROR {symbol}: {exc}")

    save_state(state)
    if failures:
        raise RuntimeError("One or more symbols failed: " + " | ".join(failures))


if __name__ == "__main__":
    main()
