"""Persistent Voroa supervisor for three independent strategy scanner workers.

Each strategy runs in its own subprocess concurrently. This service never trades.
"""
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from market_data import fetch_candles

ROOT = Path(__file__).resolve().parent
INTERVAL_SECONDS = 20
DATA_INTERVALS = ("5min", "1hour", "1min")
REQUIRED_INTERVALS = ("5min", "1hour")
STOP = False

STRATEGIES = (
    ("main.py", "EUR/USD,XAU/USD,BTC/USD"),
    ("strategy2.py", "EUR/USD,XAU/USD,BTC/USD"),
    ("strategy3.py", "EUR/USD,XAU/USD,XAG/USD,BTC/USD"),
)


def log(message):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{stamp}] {message}", flush=True)


def request_stop(signum, frame):
    global STOP
    STOP = True
    log(f"Received stop signal {signum}; finishing current strategy.")


def run_strategy(script, symbols):
    path = ROOT / script
    if not path.is_file():
        log(f"SKIP {script}: file not found at {path}")
        return 2

    env = os.environ.copy()
    env["SYMBOLS"] = symbols
    env["PYTHONUNBUFFERED"] = "1"

    log(f"START {script} | symbols={symbols}")
    try:
        result = subprocess.run(
            [sys.executable, "-u", str(path)],
            cwd=str(ROOT),
            env=env,
            check=False,
            timeout=240,
        )
    except subprocess.TimeoutExpired:
        log(f"ERROR {script}: exceeded 240-second timeout")
        return 124
    except Exception as exc:
        log(f"ERROR {script}: {type(exc).__name__}: {exc}")
        return 1

    if result.returncode == 0:
        log(f"FINISH {script}: exit code 0")
    else:
        log(f"ERROR {script}: exit code {result.returncode}")
    return result.returncode


def warm_market_data():
    """Fetch shared candle sets concurrently before the strategies scan.

    The strategy scripts then read the same short-lived on-disk cache instead
    of serially waiting on the quote API for every market/timeframe.
    """
    symbols = sorted({
        symbol.strip()
        for _, configured in STRATEGIES
        for symbol in configured.split(",")
        if symbol.strip()
    })
    jobs = {
        (symbol, interval): None
        for symbol in symbols
        for interval in DATA_INTERVALS
    }
    started = time.monotonic()
    succeeded = set()
    failures = []

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as pool:
        pending = {
            pool.submit(fetch_candles, symbol, interval=interval): (symbol, interval)
            for symbol, interval in jobs
        }
        for future in as_completed(pending):
            symbol, interval = pending[future]
            try:
                future.result()
                succeeded.add((symbol, interval))
            except Exception as exc:
                failures.append((symbol, interval, f"{type(exc).__name__}: {exc}"))
                log(f"DATA ERROR {symbol} {interval}: {type(exc).__name__}: {exc}")

    available = {
        symbol for symbol in symbols
        if all((symbol, interval) in succeeded for interval in REQUIRED_INTERVALS)
    }
    elapsed = time.monotonic() - started
    log(
        f"Market-data prefetch: {len(succeeded)}/{len(jobs)} datasets ready "
        f"in {elapsed:.1f}s; {len(available)}/{len(symbols)} markets available."
    )
    return available


def run_cycle():
    failures = []
    available = warm_market_data()
    if STOP:
        return
    if not available:
        log("CYCLE skipped: no market has a complete fresh 5m/1h data set.")
        return

    # Run one isolated process per strategy. A slow scanner cannot hold up
    # the other strategies; shared alert deduplication is protected by a file lock.
    jobs = {}
    with ThreadPoolExecutor(max_workers=len(STRATEGIES)) as pool:
        for script, configured_symbols in STRATEGIES:
            symbols = ",".join(
                symbol for symbol in configured_symbols.split(",")
                if symbol in available
            )
            if not symbols:
                log(f"SKIP {script}: no markets have complete fresh data.")
                continue
            jobs[pool.submit(run_strategy, script, symbols)] = script

        for future in as_completed(jobs):
            script = jobs[future]
            try:
                code = future.result()
            except Exception as exc:
                code = 1
                log(f"ERROR {script}: worker future failed: {type(exc).__name__}: {exc}")
            if code:
                failures.append(f"{script}={code}")
    if failures:
        log("CYCLE completed with strategy errors: " + ", ".join(failures))
    elif len(available) < len({
        symbol.strip()
        for _, configured in STRATEGIES
        for symbol in configured.split(",")
        if symbol.strip()
    }):
        log("CYCLE completed with partial market coverage; failed datasets were skipped safely.")
    else:
        log("CYCLE completed: all three strategies exited successfully")


def main():
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    log("Voroa supervisor started: three independent strategy workers.")
    log(f"Scan interval: {INTERVAL_SECONDS} seconds.")
    log("Markets: EUR/USD, XAU/USD, XAG/USD, BTC/USD.")
    log("Mode: alert-only; no broker orders are placed.")

    if os.getenv("SEND_TEST_EMAIL_ON_START", "").strip().lower() in {"1", "true", "yes"}:
        log("Manual email test requested.")
        test_code = run_strategy("send_test_email.py", "")
        log(f"MANUAL EMAIL TEST: {'successful' if test_code == 0 else f'failed with exit code {test_code}'}.")

    if os.getenv("SEND_TEST_ALERT_ON_START", "").strip().lower() in {"1", "true", "yes"}:
        log("Manual BUY-alert pipeline test requested. No trade will be placed.")
        test_code = run_strategy("send_test_alert.py", "")
        log(f"BUY-ALERT PIPELINE TEST: {'successful' if test_code == 0 else f'failed with exit code {test_code}'}.")

    next_run = time.monotonic()
    while not STOP:
        cycle_started = time.monotonic()
        run_cycle()
        if STOP:
            break

        next_run = max(next_run + INTERVAL_SECONDS, time.monotonic())
        delay = max(0, next_run - time.monotonic())
        log(f"Next scan cycle in {int(delay)} seconds.")
        while delay > 0 and not STOP:
            time.sleep(min(1, delay))
            delay = next_run - time.monotonic()

    log("Voroa alert worker stopped.")


if __name__ == "__main__":
    main()
