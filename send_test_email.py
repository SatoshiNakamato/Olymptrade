import os
import smtplib
from email.message import EmailMessage

sender = os.environ["GMAIL_SENDER"]
password = os.environ["GMAIL_APP_PASSWORD"]
recipient = os.environ.get("ALERT_TO", "Ineed2pnaga@gmail.com")

msg = EmailMessage()
msg["Subject"] = "TEST — Olymptrade market alerts email delivery"
msg["From"] = sender
msg["To"] = recipient
msg.set_content(
    "This is a test email from the Olymptrade market-alerts GitHub Actions workflow.\n\n"
    "Gmail SMTP authentication and delivery were requested for testing. "
    "This is NOT a BUY or SELL signal. No trade was placed.\n"
)

with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
    smtp.login(sender, password)
    smtp.send_message(msg)

print(f"Test email sent successfully to {recipient}")
