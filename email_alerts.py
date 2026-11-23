import os
import smtplib
from email.message import EmailMessage

def send_alert(signal):
    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("ALERT_TO", "Ineed2pnaga@gmail.com")
    msg = EmailMessage()
    msg["Subject"] = f'{signal["action"]} alert: {signal["symbol"]}'
    msg["From"], msg["To"] = sender, recipient
    msg.set_content(
        f'{signal["action"]} setup detected for {signal["symbol"]}\n\n'
        f'Last closed 5m candle: {signal["candle_time"]}\n'
        f'Indicative entry: {signal["price"]:.5f}\n'
        f'ATR-based stop: {signal["stop_loss"]:.5f}\n'
        f'ATR-based target: {signal["take_profit"]:.5f}\n'
        f'RSI: {signal["rsi"]:.1f} | score: {signal["score"]}\n'
        f'Reasons: {signal["reason"]}\n\n'
        'Automated technical-analysis alert only. Verify live quote, spread, session and risk.'
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
