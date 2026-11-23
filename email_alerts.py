import os
import smtplib
from email.message import EmailMessage

def send_alert(signal):
    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("ALERT_TO", "Ineed2pnaga@gmail.com")
    action = signal["action"]
    msg = EmailMessage()
    msg["Subject"] = f'{action}: {signal["symbol"]} | entry {signal["price"]:.5f}'
    msg["From"], msg["To"] = sender, recipient
    if action == "BUY NOW":
        instruction = "BUY NOW only if your broker's live ask is still near the entry price."
    else:
        instruction = "SELL NOW only if your broker's live bid is still near the entry price."
    msg.set_content(
        f'{instruction}\n\n'
        f'Market: {signal["symbol"]}\nAction: {action}\n'
        f'Indicative entry: {signal["price"]:.5f}\n'
        f'Stop-loss reference: {signal["stop_loss"]:.5f}\n'
        f'Take-profit 1 (1R): {signal["take_profit_1"]:.5f}\n'
        f'Take-profit 2 (2R): {signal["take_profit_2"]:.5f}\n'
        f'Suggested holding window: {signal["hold_window"]}\n'
        f'4h trend: {signal["four_hour_trend"]}\n'
        f'RSI: {signal["rsi"]:.1f}\n'
        f'Confirmed candle (UTC): {signal["candle_time"]}\n'
        f'Signal validity: {signal["valid_until"]}\n'
        f'Reason: {signal["reason"]}\n\n'
        'Execution checklist: verify the live broker quote and spread before entry. '
        'If price has moved materially away from the indicative entry, skip the trade. '
        'Consider taking partial profit at TP1 and managing the remainder toward TP2. '
        'The holding window is a technical estimate, not a prediction or guarantee. '
        'This alert does not know whether you entered a position and does not manage or close trades.'
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
