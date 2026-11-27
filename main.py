import json
import os
import tempfile
from datetime import datetime, timezone, timedelta

from market_data import fetch_candles
from signals import evaluate
from email_alerts import send_alert, send_exit_alert
from market_filters import london_session_status, new_york_session_status, usd_high_impact_news_status
from plan_notifications import monitor_and_notify
from email_alerts import send_tp1_alert
from alert_dedupe import is_duplicate, record_sent, send_if_not_duplicate

STATE_PATH = ".signal_state.json"


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    """Atomically persist scanner state so worker restarts cannot leave partial JSON."""
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=os.path.dirname(os.path.abspath(STATE_PATH)),
            prefix=".signal_state.", suffix=".tmp", delete=False
        ) as handle:
            json.dump(state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, STATE_PATH)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _as_utc(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def main():
    symbols = [s.strip() for s in os.getenv("SYMBOLS", "XAU/USD,XAG/USD,US500").split(",") if s.strip()]
    state, now = load_state(), datetime.now(timezone.utc)
    in_session, session_reason = london_session_status(now)
    failures = []

    for symbol in symbols:
        try:
            candles = fetch_candles(symbol, interval="5min")
            hourly = fetch_candles(symbol, interval="1hour")
            previous = state.get(symbol, {})
            plan = previous.get("active_plan")
            if plan:
                events = monitor_and_notify(candles, plan, now, symbol)
                for event in events:
                    if event["type"] == "tp1":
                        print(f"TP1 price alert sent: {symbol}")
                    elif event["type"] == "profit_goal":
                        print(f"Suggested rupee objective price reached: {symbol}")
                    elif event["type"] == "status":
                        print(f"{event['status']} reminder sent: {symbol}")
                    else:
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
            if symbol == "US500":
                market_open, market_reason = new_york_session_status(now)
            else:
                market_open, market_reason = in_session, session_reason
            if not is_crypto and not market_open:
                print(f"{symbol} new entries skipped: {market_reason}")
                continue
            if symbol in {"EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "XAU/USD", "XAG/USD", "US500"}:
                news_ok, news_reason = usd_high_impact_news_status(now)
                if not news_ok:
                    print(f"{symbol} new entry skipped: {news_reason}")
                    continue

            signal = evaluate(symbol, candles, now, raw_1h=hourly)
            print(json.dumps(signal, default=str))
            if signal["action"] == "WAIT":
                continue

            detected_at = datetime.now(timezone.utc)
            print(
                f"[{detected_at.isoformat(timespec='milliseconds')}] "
                f"Strategy 1 SIGNAL_DETECTED {symbol} {signal['action']} "
                f"confirmation_candle={signal.get('candle_time')}",
                flush=True,
            )
            key = f'{symbol}:{signal["action"]}:{signal["candle_time"]}'
            last_time = previous.get("sent_at")
            cooldown_ok = (not last_time or
                           now - _as_utc(last_time) >= timedelta(minutes=20) or
                           previous.get("action") != signal["action"])
            if is_duplicate(symbol, signal["action"], now):
                print(f"Cross-strategy duplicate suppressed: {symbol}")
                continue
            if previous.get("key") == key or not cooldown_ok:
                print(f"Duplicate/cooldown suppressed: {symbol}")
                continue

            sent = send_if_not_duplicate(
                symbol, signal["action"], now, lambda: send_alert(signal),
                strategy="Strategy 1",
            )
            if not sent:
                print(f"Cross-strategy duplicate suppressed at send lock: {symbol}")
                continue
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
