"""Transactional plan monitoring notifications.

If SMTP fails, restore the plan checkpoint so the same event can be retried
on the next scan instead of being silently marked as already notified.
"""
from email_alerts import send_exit_alert, send_tp1_alert, send_hold_alert
from plan_monitor import monitor_plan


def monitor_and_notify(raw5, plan, now, symbol):
    checkpoint_before = plan.get("last_checked_candle", plan["candle_time"])
    tp1_before = plan.get("tp1_notified", False)
    had_tp1_flag = "tp1_notified" in plan
    status_before = plan.get("last_status_alert_at")
    had_status_flag = "last_status_alert_at" in plan

    events = monitor_plan(raw5, plan, now)
    tp1_delivered = False
    try:
        for event in events:
            if event["type"] == "tp1":
                send_tp1_alert(
                    symbol, plan["action"], plan["take_profit_1"],
                    plan, event["time"]
                )
                tp1_delivered = True
                plan["tp1_notified"] = True
            elif event["type"] == "status":
                send_hold_alert(
                    symbol, plan["action"], event["status"],
                    event["price"], plan, event["time"]
                )
            else:
                send_exit_alert(
                    symbol, plan["action"], event["reason"],
                    event["price"], plan
                )
    except Exception:
        plan["last_checked_candle"] = checkpoint_before
        if tp1_delivered:
            plan["tp1_notified"] = True
        elif had_tp1_flag:
            plan["tp1_notified"] = tp1_before
        else:
            plan.pop("tp1_notified", None)
        if had_status_flag:
            plan["last_status_alert_at"] = status_before
        else:
            plan.pop("last_status_alert_at", None)
        raise

    return events
