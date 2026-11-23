# EUR/USD + Gold Email Signal Alerts

Autonomous, alert-only technical analysis for EUR/USD and spot gold (XAU/USD). It does not connect to Olymptrade, place orders, or guarantee profitable trades.

## Signal logic
- GitHub Actions checks every 5 minutes (best-effort scheduling).
- One 5-minute candle-history request per symbol is used to build closed 5m, 15m, 1h and 4h candles locally.
- A BUY NOW / SELL NOW alert requires aligned 5m, 15m and 1h EMA20/EMA50 trends, a closed 5m breakout candle, MACD confirmation and an RSI momentum range.
- The 4h trend affects the suggested holding window.
- Emails include an indicative entry, a narrow entry zone, ATR stop-loss, TP1 (1R), TP2 (2R), and a suggested 1–2h or 2–4h holding window.
- No email is sent when the setup is not confirmed.
- Duplicate alerts are suppressed; same-direction alerts have a cooldown.

## Required GitHub Actions secrets
Add these under Settings -> Secrets and variables -> Actions:

| Secret | Value |
|---|---|
| TWELVEDATA_API_KEY | Your Twelve Data API key |
| GMAIL_SENDER | Gmail account used to send the alert |
| GMAIL_APP_PASSWORD | Google App Password for that sender |

Recipient is Ineed2pnaga@gmail.com. Never commit credentials or paste them into chat. Gmail App Passwords require 2-Step Verification.

## Setup
1. Create a Twelve Data account and obtain an API key. Check that your plan supports 5-minute EUR/USD and XAU/USD time series and the requested history depth.
2. Enable 2-Step Verification on the sender Gmail account and create an App Password.
3. Add the three repository Actions secrets above.
4. Open Actions -> Market signal email alerts -> Run workflow to test.
5. Check the run log and inbox. Keep Actions enabled for scheduled scans.

## API usage and automation
The workflow makes two market-data requests per 5-minute scan (one per symbol), approximately 576 requests/credits per day if each request costs one credit. Confirm this fits your Twelve Data plan and leave room for manual tests. GitHub cron can start late or be skipped; it is not a tick-by-tick or guaranteed real-time service.

## Trading and execution limitations
A BUY NOW / SELL NOW email is a confirmed technical setup at the last closed 5-minute candle, not certainty. Verify the live Olymptrade quote and spread; skip if it has moved outside the emailed entry zone or the validity window has expired. TP and stop levels are reference prices, not broker orders. The holding window is an estimate based on trend alignment, not a promise that the target will be reached. The bot does not know whether you entered and does not manage or close positions. Test on demo first.
