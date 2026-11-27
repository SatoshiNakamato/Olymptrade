# XAU/USD, XAG/USD and US500 Market Signal Email Alerts

Alert-only market analysis for exactly three approved markets: gold (XAU/USD), silver (XAG/USD), and US500. All other markets are rejected by a shared code-level whitelist. The bot does not connect to Olymptrade, place orders, or guarantee profitable trades.

## Strategy 1 — 1h structure + 15m BOS zone + 5m sweep/MSS

The original scanner evaluates completed candles and requires aligned 1-hour swing/internal structure, a fresh 15-minute demand/supply zone in the correct half of the range, a 5-minute liquidity sweep, candle-close market-structure shift, and pullback. It requires at least 1:2 risk/reward or waits.

## Strategy 2 — 15m trend + zone + lower-timeframe MSS

The second scanner looks for 15-minute trend alignment, a return to a displacement-origin zone, and a completed lower-timeframe market-structure shift. It calculates a swing-based stop and 2R target. It can use 1-minute confirmation when the 5-minute confirmation is too far from the zone.

## Strategy 3 — three-market 1h/15m trend pullback continuation

The third scanner evaluates only **XAU/USD, XAG/USD, and US500**. Each selected market has its own signal state and duplicate/cooldown protection.

1. 1-hour EMA20/EMA50 and price must agree on direction.
2. 15-minute EMA20/EMA50 and price must agree with the 1-hour trend.
3. Price must recently pull back to the 15-minute EMA20 reference on the 5-minute chart.
4. A completed 5-minute candle must confirm continuation by closing beyond the previous candle's high (BUY) or low (SELL).
5. 5-minute RSI must be inside the direction-specific confirmation band.
6. Stop is derived from the recent 5-minute swing and ATR; TP1 is 1R and TP2 is 2R.
7. Each alert expires at the next 5-minute candle close. Recheck the live quote and do not enter on an expired alert.

The rules are two-sided and can produce BUY, SELL, or WAIT separately for each selected market. XAU/USD and XAG/USD use the London-session filter; US500 uses the New York cash-session filter. USD high-impact news blocks new alerts for all three selected markets. No fixed price levels are hard-coded.

## Gold and silver entry plan

See [`PRECIOUS_METALS_STRATEGY.md`](PRECIOUS_METALS_STRATEGY.md) for the separate 1h trend + 15m break + 5m retest plan, including a hard 90-minute retest expiry, entry validity, stop/targets, cancellation rules and demo-testing checklist. This is a strategy specification only; it is not part of the automated worker until implemented and tested.

## Session and news filters

- XAU/USD and XAG/USD entries are permitted only during 08:00–17:00 Europe/London time, Monday to Friday.
- US500 entries are permitted only during 09:30–16:00 America/New_York time, Monday to Friday.
- All three selected markets are blocked from 15 minutes before to 15 minutes after high-impact USD calendar events.
- The calendar filter fails closed: if the calendar cannot be retrieved or parsed, the relevant entry alerts are skipped.
- The calendar feed is a third-party source; verify major event times against a trusted economic calendar.

## Position suggestions and monitoring

Each confirmed BUY/SELL alert includes a rule-based quality score, suggested INR investment and multiplier, a gross rupee profit objective, and approximate gross P/L at TP1, TP2 and the stop. Current suggestions use ₹100/₹200 stakes; non-US500 instruments use 30x/50x/100x tiers, while US500 uses 10x/15x/20x. These are suggestions only: choose any multiplier and stake actually available to you. The score is not a win probability and does not change whether a strategy signal is sent.

Rupee calculations are estimates based on percentage price movement and exclude Olymptrade commission, spread, currency conversion and execution differences. If you choose a different stake or multiplier, the rupee estimates and target price change. At the rupee objective or strategy TP2, whichever comes first, check actual broker P/L and close the full position manually if appropriate. TP1, hourly HOLD/MONITOR reminders, stop/TP2 exit alerts and review-window alerts monitor theoretical chart levels only.

The bot does not connect to Olymptrade, cannot see your actual fills, selected multiplier, open positions or realized P/L, and cannot place, change or close orders. It never blocks a valid strategy signal because of the optional sizing suggestion. Use a demo account for forward testing and verify every quote, spread, entry window, stop and target.

Holding/review windows are estimates based on ATR and target distance, not guaranteed durations. GitHub Actions is a separate manual/CI workflow, not the Voroa real-time worker; its scans are not real-time and can miss intrabar setups. No strategy promises a fixed number of signals per day; strict filters may correctly produce no alert on some days.

## Automation and data

- Voroa uses two independent Background Workers: `python worker.py` runs Strategies 1 and 2; `python strategy3_worker.py` runs Strategy 3 only.
- Each worker uses one Python process and three concurrent market tasks for XAU/USD, XAG/USD and US500. The Strategy 3 worker has its own `.voroa_strategy3` state directory; the Strategies 1+2 worker uses `.voroa_agents`.
- Both workers enforce the same London entry session for gold/silver and New York cash entry session for US500. Existing plans remain monitored outside entry sessions.
- Strategy 3's dedicated worker imports any accessible legacy Strategy 3 per-market state from `.voroa_agents` or `.strategy3_state.json` on first start, without deleting the source.
- `watchlist.py` is the authoritative whitelist. Environment-variable overrides and direct strategy calls cannot add markets outside XAU/USD, XAG/USD, and US500.
- The worker scans during the selected markets' London/New York sessions and pauses when both sessions are closed and no active plans need monitoring.
- The shared Biquote adapter supplies completed 1-minute, 5-minute and 1-hour candles; 1-minute data is requested only by Strategy 2 when its existing fallback condition is met.
- The central worker preserves the three existing strategy state files and migrates per-market state from the previous `.voroa_agents` directories without deleting the originals.

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
5. Forward-test every strategy on a demo account before considering live use.
