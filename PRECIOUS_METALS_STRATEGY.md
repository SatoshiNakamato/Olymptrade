# Precious-metals strategy: 1h trend + 15m break + 5m retest

**Markets:** XAU/USD (gold) and XAG/USD (silver)  
**Purpose:** A rules-based, alert-only entry plan. It does not place, monitor, or close broker orders.  
**Status:** Strategy specification only; not yet implemented as an automated scanner and not backtested. Forward-test on demo before using it.

This plan is designed to avoid two bad extremes: entering before a setup is confirmed, and waiting indefinitely for an old setup. Every setup has a fixed expiry.

## Timeframes and sequence

1. **1-hour — directional filter.** Use completed candles only. Bullish context requires EMA20 above EMA50 and the latest close above EMA20. Bearish context requires EMA20 below EMA50 and the latest close below EMA20. Otherwise, WAIT.
2. **15-minute — structural break.** In bullish context, wait for a completed 15m candle to close above the most recent confirmed swing high. In bearish context, wait for a completed 15m candle to close below the most recent confirmed swing low. The break must exceed the level by at least 0.10 × 15m ATR(14), to avoid treating a marginal touch as a break.
3. **15-minute — retest window.** After the break candle closes, allow at most the next six completed 15m candles (90 minutes) for price to revisit the broken level. The broken resistance should act as support for BUY; broken support should act as resistance for SELL.
4. **5-minute — entry trigger.** After the retest, require a completed 5m rejection candle in the trade direction that also closes beyond the previous completed 5m candle's high (BUY) or low (SELL). Do not enter merely because price touches the level.
5. **Entry validity.** The trigger is valid only until the next 5m candle closes. If the price has already moved more than 0.25 × 5m ATR away from the retest zone, skip rather than chase.

## Entry, stop and targets

- **BUY:** Enter only after the 5m bullish rejection/continuation close. Place the protective stop below the retest swing low, with a volatility buffer of 0.20 × 5m ATR(14).
- **SELL:** Enter only after the 5m bearish rejection/continuation close. Place the protective stop above the retest swing high, with a volatility buffer of 0.20 × 5m ATR(14).
- Define risk per unit as the absolute distance between entry and stop.
- **TP1:** 1R. Consider reducing exposure here; do not widen the original stop.
- **TP2:** 2R, provided the next opposing 15m structure does not block the target. If there is not room for at least 2R after realistic spread and fees, skip the trade.
- Never move a stop farther away to avoid taking a loss. Any move to break-even or trailing rule must be specified and tested before automation.

## Hard expiry and cancellation rules

Cancel the setup and return to WAIT when any of these happens:

- No retest within six completed 15m candles (90 minutes) after the break.
- A completed 15m candle closes back through the broken level, invalidating the break.
- The 1h EMA trend filter no longer agrees with the trade direction.
- The 5m entry trigger does not occur during the retest; do not keep an old retest alive.
- Entry is no longer close enough to the retest zone under the 0.25 ATR chase rule.
- A high-impact USD event is within 30 minutes before or after the proposed entry, or the market is in a thin/closed session.
- The stop is too wide for the account's predefined risk limit, or the target offers less than 2R.

A cancelled setup is not a missed obligation. Wait for a new 15m structural break before considering another setup.

## Risk controls

- Forward-test on demo first and record every qualifying setup, skipped setup, entry, stop, target, spread, slippage and result.
- Keep planned loss small and fixed (a conservative starting ceiling is 0.5% of account equity per trade); never size a position from the desired profit.
- Gold and silver can move together. Treat simultaneous positions as correlated exposure and cap their combined planned loss, rather than risking the full per-trade amount on each.
- Do not assume a fixed rupee profit from leverage. Actual P/L depends on contract specifications, position size, conversion, spread, fees and slippage.
- Do not force a minimum number of alerts per day. A valid WAIT is preferable to a fabricated signal.

## Review and holding window

This is an intraday setup, not an instruction to hold for a fixed number of hours. Review after each completed 15m candle while a setup is forming and after each completed 1h candle while a position is open. The stop-loss and target—not an estimated 4h, 12h or 24h timer—define the trade's risk and exit plan. Close or reduce exposure according to the tested exit rules; do not extend a trade just because the estimated holding window has not elapsed.

## Automation and testing checklist

Before adding this as an automated strategy:

- [ ] Implement closed-candle calculations and confirm there is no look-ahead bias in swing detection.
- [ ] Add explicit 90-minute retest expiry and 5-minute entry expiry.
- [ ] Add spread/news/session checks using data sources that are actually available to the worker.
- [ ] Unit-test BUY, SELL, no-retest expiry, failed retest, stale entry, trend reversal and insufficient-2R cases.
- [ ] Backtest gold and silver separately with realistic spread, commission and slippage; report sample size, expectancy, drawdown and out-of-sample results.
- [ ] Forward-test alerts on demo before enabling live decision use.

## Important limitation

This is a testable trading framework, not a verified edge or a live market call. Break-and-retest patterns can fail; confirmation reduces neither risk nor losses to zero. See [Investopedia's discussion of breakout/retest confirmation and stop placement](https://www.investopedia.com/articles/technical/121201.asp) and the [break-and-retest rule sequence and limitations](https://fxglory.com/learn/forex-strategies/break-and-retest-strategy-forex/). The historical results of any one model should not be generalized to this plan.
