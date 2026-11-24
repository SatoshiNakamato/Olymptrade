import os
import smtplib
from email.message import EmailMessage


def _price(value, symbol):
    return f"{value:.5f}" if symbol == "EUR/USD" else f"{value:.2f}"


def send_alert(signal, test_mode=False):
    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("ALERT_TO", "Ineed2pnaga@gmail.com")
    symbol, action = signal["symbol"], signal["action"]
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
        f'Reason: {signal["reason"]}\n'
        + ('' if signal.get("trend_aligned", False) else 'CAUTION: Internal direction is counter to the 1h swing trend; this is a smaller countertrend move, not a higher-timeframe trend-following setup.\n')
        + '\n'
        'RISK RULES: risk only 0.5–1% of account equity per trade, with size calculated from the actual stop distance. '
        'Stop for the day after a 2% loss, 5% profit, or 3 trades. These daily limits are reminders only: '
        'this alert-only bot cannot see your account balance, filled orders, or realized P/L and cannot enforce them. '
        'Do not enter if the quote has moved outside the entry zone, the signal expired, the spread is abnormal, '
        'or a major USD news blackout is active. No setup is guaranteed. The bot does not place or manage orders.'
    )

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
        f"TP1 (1R) PRICE ALERT — {symbol}\\n"
        f"Plan direction: {action}\\n"
        f"TP1 level touched: {_price(price, symbol)}\\n"
        f"Stop-loss reference: {_price(plan['stop_loss'], symbol)}\\n"
        f"TP2 reference: {_price(plan['take_profit_2'], symbol)}\\n"
        f"Completed 5m candle (UTC): {event_time}\\n\\n"
        "RECOMMENDATION: If you entered and the live broker quote confirms TP1, consider "
        "securing a partial profit manually. For any remaining position, review whether moving "
        "the stop toward breakeven is appropriate for your execution costs and market conditions. "
        "Do not assume TP1 guarantees TP2. "
        "The bot cannot verify your entry, filled quantity, or realized profit and cannot change "
        "stops, take partial profit, or close orders. If you did not enter, ignore this alert."
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
        "This monitors the theoretical alert plan only. It cannot verify that you entered, "
        "or close a position on Olymptrade. Check your actual open position and broker quote."
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
