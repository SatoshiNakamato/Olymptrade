import os
import smtplib
from email.message import EmailMessage

from trade_advisor import recommend_trade, format_trade_advice


def _price(value, symbol):
    if symbol in {"EUR/USD", "GBP/USD", "AUD/USD"}:
        return f"{value:.5f}"
    if symbol == "USD/JPY":
        return f"{value:.3f}"
    return f"{value:.2f}" if symbol in {"BTC/USD", "US500"} else f"{value:.3f}"


def send_alert(signal, test_mode=False):
    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("ALERT_TO", "Ineed2pnaga@gmail.com")
    symbol, action = signal["symbol"], signal["action"]
    advice = recommend_trade(signal)
    signal["trade_advice"] = advice
    low = signal.get("entry_zone_low", signal["price"])
    high = signal.get("entry_zone_high", signal["price"])
    msg = EmailMessage()
    msg["Subject"] = f'{action}: {symbol} | SMC setup confirmed'
    msg["From"], msg["To"] = sender, recipient
    msg.set_content(
        f'{action} — verify the live broker quote before entering.\n\n'
        f'Market: {symbol}\nAction: {action}\n'
        f'Entry reference: {_price(signal["price"], symbol)}\n'
        f'5m pullback entry zone: {_price(low, symbol)} to {_price(high, symbol)}\n'
        f'Stop-loss: {_price(signal["stop_loss"], symbol)}\n'
        f'TP1 (1R): {_price(signal["take_profit_1"], symbol)}\n'
        f'TP2 / opposing liquidity: {_price(signal["take_profit_2"], symbol)}\n'
        f'Risk-to-reward: 1:{signal["risk_reward"]:.2f}\n'
        f'Suggested review/holding window: {signal.get("hold_hours", 5)} hours (estimate, not a guaranteed duration)\n'
        f'1h swing trend: {signal["swing_trend_1h"]}\n'
        f'1h internal structure: {signal["internal_trend_1h"]}\n'
        f'1h swing/internal aligned: {signal.get("trend_aligned", False)}\n'
        f'Target method: {signal.get("target_type", "2R")}\n'
        f'15m zone: {signal["zone_15m"]["side"]} '
        f'({_price(signal["zone_15m"]["low"], symbol)}–{_price(signal["zone_15m"]["high"], symbol)})\n'
        f'Liquidity sweep: {signal["liquidity_sweep"]}\n'
        f'Market shift: {signal["market_shift"]}\n'
        f'Confirmed 5m candle (UTC): {signal["candle_time"]}\n'
        f'Valid until: {signal["valid_until"]}\n\n'
        f'Reason: {signal["reason"]}\n\n'
        + ('' if signal.get("trend_aligned", False) else 'CAUTION: Internal direction is counter to the 1h swing trend; this is a smaller countertrend move, not a higher-timeframe trend-following setup.\n')
        + '\n' + format_trade_advice(advice, lambda value: _price(value, symbol)) + '\n\n'
        'Check the live Olymptrade quote, spread and signal expiry before entering. '
        'This bot cannot see your actual fill, selected multiplier, open position or realized P/L. '
        'It never places or closes orders.    )

    if test_mode:
        msg.replace_header("Subject", f"TEST ONLY — alert pipeline check | {symbol}")
        msg.set_content(
            "TEST ONLY — NO TRADE.\n\n"
            "This message verifies the actual send_alert() email delivery function.\n"
            "The signal data is artificial. It is not a market recommendation.\n"
            "No broker order was placed.\n"
        )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)



def send_tp1_alert(symbol, action, price, plan, event_time):
    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("ALERT_TO", "Ineed2pnaga@gmail.com")
    msg = EmailMessage()
    msg["Subject"] = f"TP1 REACHED: {symbol} | {action} plan"
    msg["From"], msg["To"] = sender, recipient
    msg.set_content(
        f"TP1 (1R) PRICE ALERT — {symbol}\n"
        f"Plan direction: {action}\n"
        f"TP1 level touched: {_price(price, symbol)}\n"
        f"Stop-loss reference: {_price(plan['stop_loss'], symbol)}\n"
        f"TP2 reference: {_price(plan['take_profit_2'], symbol)}\n"
        f"Completed 5m candle (UTC): {event_time}\n\n"
        "RECOMMENDATION: If you entered, check your actual Olymptrade P/L against the rupee objective "
        "in the original alert. If your objective is reached, close the full position manually. "
        "Otherwise, HOLD only if your chart thesis remains valid; monitor the stop and TP2. "
        "TP1 does not guarantee TP2. The bot cannot verify your fill, chosen multiplier or P/L, "
        "and cannot change or close orders. If you did not enter, ignore this alert."
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)


def send_exit_alert(symbol, action, reason, price, plan):
    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("ALERT_TO", "Ineed2pnaga@gmail.com")
    msg = EmailMessage()
    msg["Subject"] = f"TRADE PLAN EXIT: {symbol} | {reason}"
    msg["From"], msg["To"] = sender, recipient
    msg.set_content(
        f"EXIT / REVIEW ALERT — {symbol}\n"
        f"Plan direction: {plan['action']}\n"
        f"Reason: {reason}\n"
        f"Latest closed 5m price: {_price(price, symbol)}\n"
        f"Planned entry reference: {_price(plan['price'], symbol)}\n"
        f"Planned stop loss: {_price(plan['stop_loss'], symbol)}\n"
        f"Planned take profit: {_price(plan['take_profit_2'], symbol)}\n\n"
        "CLOSE MANUALLY if your position is still open and this exit condition applies. "
        "This monitors theoretical chart levels only; it cannot verify your fill or close "
        "a position on Olymptrade. Check the actual broker quote and P/L."
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)



def send_hold_alert(symbol, action, status, price, plan, event_time):
    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("ALERT_TO", "Ineed2pnaga@gmail.com")
    advice = plan.get("trade_advice", {})
    msg = EmailMessage()
    msg["Subject"] = f"{status}: {symbol} | {action} plan review"
    msg["From"], msg["To"] = sender, recipient
    msg.set_content(
        f"{status} / CHART REVIEW — {symbol} {action}\n"
        f"Latest closed 5m price: {_price(price, symbol)}\n"
        f"Entry reference: {_price(plan['price'], symbol)}\n"
        f"Stop-loss: {_price(plan['stop_loss'], symbol)}\n"
        f"TP1: {_price(plan['take_profit_1'], symbol)}\n"
        f"TP2: {_price(plan['take_profit_2'], symbol)}\n"
        f"Review time (UTC): {event_time}\n"
        + (f"Suggested objective: +₹{advice.get('profit_goal_inr')} "
           f"using suggested ₹{advice.get('stake_inr')} / {advice.get('multiplier')}x settings.\n"
           if advice else "")
        + ("MONITOR: price has moved materially against the planned direction. Recheck the live "
           "chart and be ready to close manually if the setup is weakening.\n"
           if status == "MONITOR" else
           "HOLD REVIEW: price has not reached the planned exit levels. Continue only while "
           "your own chart review confirms the setup remains valid.\n")
        + "This is a theoretical plan reminder, not a view of your actual position or broker P/L. "
        "The bot cannot verify entry, change stops, or close orders. If you did not enter, ignore it."
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
