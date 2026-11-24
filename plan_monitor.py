"""Shared monitoring for alert-only trade plans; never places or closes orders."""
import pandas as pd


def monitor_plan(raw5, plan, now):
    data = raw5.copy()
    data["datetime"] = pd.to_datetime(data["datetime"], utc=True)
    closed = data[data["datetime"] + pd.Timedelta(minutes=5) <= pd.Timestamp(now)].sort_values("datetime")
    entry_time = pd.Timestamp(plan["candle_time"])
    checkpoint = pd.Timestamp(plan.get("last_checked_candle", plan["candle_time"]))
    if entry_time.tzinfo is None: entry_time = entry_time.tz_localize("UTC")
    if checkpoint.tzinfo is None: checkpoint = checkpoint.tz_localize("UTC")
    bars = closed[(closed["datetime"] > entry_time) & (closed["datetime"] > checkpoint)]
    buy = plan["action"] == "BUY"
    events = []
    for _, bar in bars.iterrows():
        high, low, close = float(bar.high), float(bar.low), float(bar.close)
        stop = float(plan["stop_loss"])
        tp1, tp2 = float(plan["take_profit_1"]), float(plan["take_profit_2"])
        stamp = bar.datetime.isoformat()
        if (low <= stop if buy else high >= stop):
            events.append({"type":"exit","reason":"planned stop-loss touched","price":close,"time":stamp})
            return events
        if not plan.get("tp1_notified") and (high >= tp1 if buy else low <= tp1):
            plan["tp1_notified"] = True
            events.append({"type":"tp1","reason":"TP1 (1R) level touched","price":tp1,"time":stamp})
        if (high >= tp2 if buy else low <= tp2):
            events.append({"type":"exit","reason":"planned TP2 (2R) touched","price":close,"time":stamp})
            return events
        plan["last_checked_candle"] = stamp
    created = pd.Timestamp(plan.get("created_at", plan["candle_time"]))
    if created.tzinfo is None: created = created.tz_localize("UTC")
    if pd.Timestamp(now) >= created + pd.Timedelta(hours=int(plan.get("hold_hours",5))):
        last = float(closed.close.iloc[-1]) if not closed.empty else float(plan["price"])
        events.append({"type":"exit","reason":str(plan.get("hold_hours",5))+"-hour review window reached","price":last,"time":pd.Timestamp(now).isoformat()})
    return events
