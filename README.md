# EUR/USD + Gold Email Signal Alerts

Alert-only technical analysis for EUR/USD and spot gold (XAU/USD). It does not place trades or guarantee profitable signals.

## How it works
- GitHub Actions checks every 5 minutes (scheduling is best-effort).
- Twelve Data provides 5-minute and 15-minute candles.
- EMA 20/50, RSI 14, MACD and ATR are used.
- Email is sent only when the score threshold is met; otherwise the run reports WAIT.
- Uses the last fully closed candle, indicative ATR stop/target, duplicate suppression and a 45-minute cooldown.

## Required GitHub Actions secrets
Add these under Settings -> Secrets and variables -> Actions:

| Secret | Value |
|---|---|
| TWELVEDATA_API_KEY | Your Twelve Data API key |
| GMAIL_SENDER | Gmail account used to send the alert |
| GMAIL_APP_PASSWORD | Google App Password for that sender |

Recipient is Ineed2pnaga@gmail.com. Never commit credentials or paste them into chat. Gmail App Passwords require 2-Step Verification.

## Setup
1. Create a Twelve Data account and obtain an API key. Confirm your plan supports EUR/USD and XAU/USD at both intervals.
2. Enable 2-Step Verification on the sender Gmail account and create an App Password.
3. Add the three repository Actions secrets above.
4. Open Actions -> Market signal email alerts -> Run workflow to test.
5. Check the run log and inbox. Scheduled scans then run automatically.

## Limitations
GitHub cron can start late or be skipped; this is not a real-time price trigger. Market data may differ from your broker quote and spread. Signals can be wrong. Test with a demo account before risking money. ATR levels are reference levels, not broker orders or guaranteed execution prices.
