# EUR/USD + Gold Smart-Money-Concept Email Alerts

Alert-only market analysis for EUR/USD and XAU/USD. The bot does not connect to Olymptrade, place orders, or guarantee profitable trades.

## Strategy rules

The scanner evaluates completed candles only and requires every gate below:

1. **1-hour swing structure:** higher highs/higher lows for buys or lower highs/lower lows for sells. Unclear structure means WAIT.
2. **Internal structure:** the 5-minute internal trend must align with the 1-hour swing trend. Countertrend internal moves are skipped.
3. **15-minute zone:** a fresh demand zone must be below the 1-hour dealing-range midpoint (discount) for buys; a fresh supply zone must be above it (premium). Zones are identified from displacement candles that close beyond recent structure.
4. **5-minute liquidity sweep:** price must wick beyond a recent high/low and close back inside, while interacting with the selected 15-minute zone.
5. **Market structure shift:** a completed 5-minute candle must close beyond the prior internal swing level. A wick alone is not confirmation.
6. **Pullback:** price must return to the new 5-minute origin zone after the shift.
7. **Invalidation and reward:** a close through the 15-minute zone invalidates the setup. The target must offer at least 1:2 risk-to-reward relative to the stop.

If any condition is missing, the result is WAIT and no entry email is sent.

## Session and news filters

- Alerts are permitted only during 08:00–17:00 Europe/London time, Monday to Friday.
- XAU/USD is blocked from 15 minutes before to 15 minutes after high-impact USD calendar events.
- The calendar filter fails closed: if the calendar cannot be retrieved or parsed, XAU/USD alerts are skipped.
- The calendar feed is a third-party source; verify major event times against a trusted economic calendar.

## Risk rules

The email reminds you to risk 0.5–1% of account equity per trade, calculate position size from the actual stop distance, and stop for the day after a 2% loss, 5% profit, or 3 trades.

These daily limits are **not enforced by this alert-only bot**. It cannot see your broker balance, open positions, fills, trade count, or realized daily P/L. You must enforce them yourself. Do not risk money you cannot afford to lose.

## Automation and data

- GitHub Actions checks every 5 minutes (best-effort; not guaranteed real-time).
- Twelve Data supplies 5-minute candles; the bot aggregates them into closed 15-minute and 1-hour candles locally.
- Each scan makes one market-data request per symbol, plus a calendar request for XAU/USD when inside the London session.
- Confirm your Twelve Data plan supports EUR/USD and XAU/USD and sufficient historical 5-minute data.

## Required GitHub Actions secrets

| Secret | Value |
|---|---|
| TWELVEDATA_API_KEY | Your Twelve Data API key |
| GMAIL_SENDER | Gmail account used to send alerts |
| GMAIL_APP_PASSWORD | Google App Password for that sender |

Recipient: Ineed2pnaga@gmail.com. Never commit credentials or paste them into chat. Gmail App Passwords require 2-Step Verification.

## Setup and testing

1. Add the three secrets under Settings → Secrets and variables → Actions.
2. Open Actions → Market signal email alerts → Run workflow.
3. Inspect the workflow logs for API, calendar, and Python errors.
4. Confirm email delivery before relying on scheduled alerts.
5. Forward-test on a demo account. Check every zone, entry, spread, and stop against your broker chart.

## Important limitations

This is a rule-based approximation of discretionary market-structure analysis. Swing pivots, displacement, order-block zones, and internal structure are defined mechanically; different traders may mark them differently. Historical signals do not establish profitability. The scanner can miss setups between scheduled runs, and a BUY NOW / SELL NOW email is not a guarantee or an instruction to enter after the validity window expires. The bot does not execute, monitor, or close trades.
