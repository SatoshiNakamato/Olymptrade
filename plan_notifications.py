"""Transactional plan monitoring notifications.

If SMTP fails, restore the plan checkpoint so the same event can be retried
on the next scan instead of being silently marked as already notified.
"""
from email_alerts import send_exit_alert, send_tp1_alert
from plan_monitor import monitor_plan


def monitor_and_notify(raw5, plan, now, symbol):
    checkpoint_before = plan.get("last_checked_candle", plan["candle_time"])
    tp1_before = plan.get("tp1_notified", False)
    had_tp1_flag = "tp1_notified" in plan

    events = monitor_plan(raw5, plan, now)
    try:
        for event in events:
            if event["type"] == "tp1":
                send_tp1_alert(
                    symbol, plan["action"], plan["take_profit_1"],
                    plan, event["time"]
                )
            else:
                send_exit_alert(
                    symbol, plan["action"], event["reason"],
                    event["price"], plan
                )
    except Exception:
        plan["last_checked_candle"] = checkpoint_before
        if had_tp1_flag:
            plan["tp1_notified"] = tp1_before
        else:
            plan.pop("tp1_notified", None)
        raise

    return events
