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
        f'1h swing trend: {signal["swing_trend_1h"]}\n'
        f'5m internal trend: {signal["internal_trend_5m"]}\n'
        f'15m zone: {signal["zone_15m"]["side"]} '
        f'({_price(signal["zone_15m"]["low"], symbol)}–{_price(signal["zone_15m"]["high"], symbol)})\n'
        f'Liquidity sweep: {signal["liquidity_sweep"]}\n'
        f'Market shift: {signal["market_shift"]}\n'
        f'Confirmed 5m candle (UTC): {signal["candle_time"]}\n'
        f'Valid until: {signal["valid_until"]}\n\n'
        f'Reason: {signal["reason"]}\n\n'
        'RISK RULES: risk only 0.5–1% of account equity per trade, with size calculated from the actual stop distance. '
        'Stop for the day after a 2% loss, 5% profit, or 3 trades. These daily limits are reminders only: '
        'this alert-only bot cannot see your account balance, filled orders, or realized P/L and cannot enforce them. '
        'Do not enter if the quote has moved outside the entry zone, the signal expired, the spread is abnormal, '
        'or a major USD news blackout is active. No setup is guaranteed. The bot does not place or manage orders.'
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
