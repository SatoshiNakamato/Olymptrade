import json, os
from datetime import datetime, timezone, timedelta
from market_data import fetch_candles
from signals import evaluate
from email_alerts import send_alert

CFG = {"outputsize":120,"min_atr_percent":0.015,"min_signal_score":5}
STATE_PATH = ".signal_state.json"

def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f: return json.load(f)
    except (OSError, json.JSONDecodeError): return {}

def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f: json.dump(state, f, indent=2)

def main():
    symbols = [s.strip() for s in os.getenv("SYMBOLS","EUR/USD,XAU/USD").split(",") if s.strip()]
    state, now = load_state(), datetime.now(timezone.utc)
    for symbol in symbols:
        try:
            entry = fetch_candles(symbol,"5min",CFG["outputsize"])
            confirm = fetch_candles(symbol,"15min",CFG["outputsize"])
            signal = evaluate(symbol,entry,confirm,CFG)
            print(json.dumps(signal,default=str))
            if signal["action"] == "WAIT": continue
            key = f'{symbol}:{signal["action"]}:{signal["candle_time"]}'
            previous = state.get(symbol,{})
            last_time = previous.get("sent_at")
            cooldown_ok = not last_time or now-datetime.fromisoformat(last_time)>=timedelta(minutes=45)
            if previous.get("key")==key or not cooldown_ok:
                print(f"Duplicate/cooldown suppressed: {symbol}"); continue
            send_alert(signal)
            state[symbol]={"key":key,"sent_at":now.isoformat()}
            print(f'Email sent for {symbol} {signal["action"]}')
        except Exception as exc:
            print(f"ERROR {symbol}: {exc}")
            raise
    save_state(state)

if __name__ == "__main__": main()
