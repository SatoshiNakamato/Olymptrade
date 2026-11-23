import os
import smtplib
from email.message import EmailMessage

def _price(value, symbol):
    return f"{value:.5f}" if symbol == "EUR/USD" else f"{value:.2f}"

def send_alert(signal):
    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("ALERT_TO", "Ineed2pnaga@gmail.com")
    symbol, action = signal["symbol"], signal["action"]
    entry, risk = signal["price"], signal["risk_distance"]
    band_low, band_high = entry - 0.25*risk, entry + 0.25*risk
    side = "ask" if action == "BUY NOW" else "bid"
    msg = EmailMessage()
    msg["Subject"] = f'{action}: {symbol} | entry {_price(entry, symbol)}'
    msg["From"], msg["To"] = sender, recipient
    msg.set_content(
        f'{action} — check your broker and enter only if the live {side} is inside the entry zone.\n\n'
        f'Market: {symbol}\nAction: {action}\n'
        f'Indicative entry: {_price(entry, symbol)}\n'
        f'Entry zone: {_price(band_low, symbol)} to {_price(band_high, symbol)}\n'
        f'Stop-loss reference: {_price(signal["stop_loss"], symbol)}\n'
        f'Take-profit 1 (1R): {_price(signal["take_profit_1"], symbol)}\n'
        f'Take-profit 2 (2R): {_price(signal["take_profit_2"], symbol)}\n'
        f'Suggested holding window: {signal["hold_window"]}\n'
        f'4h trend: {signal["four_hour_trend"]}\n'
        f'RSI: {signal["rsi"]:.1f}\n'
        f'Confirmed candle (UTC): {signal["candle_time"]}\n'
        f'Signal validity: {signal["valid_until"]}\n'
        f'Reason: {signal["reason"]}\n\n'
        'If the live quote is outside the entry zone or the signal validity has expired, skip the entry. '
        'Consider taking partial profit at TP1 and managing the remainder toward TP2. '
        'The holding window is a technical estimate, not a prediction or guarantee. '
        'This alert does not know whether you entered a position and does not manage or close trades.'
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
