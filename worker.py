"""Persistent Voroa supervisor for three independent strategy scanner processes.

Each strategy runs in its own OS process. This service never places trades.
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
DATA_INTERVALS = ("5min", "1hour")
REQUIRED_INTERVALS = ("5min", "1hour")
STRATEGY_TIMEOUT_SECONDS = 240
STOP = False
RUNNING_PROCESSES = {}

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
    log(f"Received stop signal {signum}; stopping active strategy processes.")
    for script, process in list(RUNNING_PROCESSES.items()):
        try:
            if process.poll() is None:
                log(f"STOP_REQUEST {script} pid={process.pid}")
                process.terminate()
        except (OSError, ProcessLookupError):
            pass


def run_strategy(script, symbols):
    """Run a one-off helper (for example, an explicitly requested email test)."""
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
            timeout=STRATEGY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        log(f"ERROR {script}: exceeded {STRATEGY_TIMEOUT_SECONDS}-second timeout")
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
    """Fetch shared candle sets concurrently before the strategy processes start."""
    symbols = sorted({
        symbol.strip()
        for _, configured in STRATEGIES
        for symbol in configured.split(",")
        if symbol.strip()
    })
    jobs = [
        (symbol, interval)
        for symbol in symbols
        for interval in DATA_INTERVALS
    ]
    started = time.monotonic()
    succeeded = set()

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


def _launch_strategy(script, symbols):
    path = ROOT / script
    if not path.is_file():
        log(f"SKIP {script}: file not found at {path}")
        return None

    env = os.environ.copy()
    env["SYMBOLS"] = symbols
    env["PYTHONUNBUFFERED"] = "1"
    try:
        process = subprocess.Popen(
            [sys.executable, "-u", str(path)],
            cwd=str(ROOT),
            env=env,
        )
    except Exception as exc:
        log(f"ERROR {script}: process launch failed: {type(exc).__name__}: {exc}")
        return None

    log(f"START {script} | pid={process.pid} | symbols={symbols}")
    return process


def _wait_for_strategy(script, process, launched_at):
    remaining = max(
        0.1,
        STRATEGY_TIMEOUT_SECONDS - (time.monotonic() - launched_at),
    )
    try:
        code = process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        log(f"ERROR {script}: exceeded {STRATEGY_TIMEOUT_SECONDS}-second runtime; terminating")
        process.terminate()
        try:
            code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            code = process.wait()

    if code == 0:
        log(f"FINISH {script}: exit code 0")
    elif STOP:
        log(f"STOPPED {script}: exit code {code}")
    else:
        log(f"ERROR {script}: exit code {code}")
    return code


def run_cycle():
    failures = []
    available = warm_market_data()
    if STOP:
        return
    if not available:
        log("CYCLE skipped: no market has a complete fresh 5m/1h data set.")
        return

    # Launch every scanner process before waiting for any scanner to finish.
    # This makes the independence explicit and prevents one slow strategy from
    # delaying the start of either of the other two.
    launched = {}
    try:
        for script, configured_symbols in STRATEGIES:
            if STOP:
                break
            symbols = ",".join(
                symbol for symbol in configured_symbols.split(",")
                if symbol in available
            )
            if not symbols:
                log(f"SKIP {script}: no markets have complete fresh data.")
                continue

            process = _launch_strategy(script, symbols)
            if process is None:
                failures.append(f"{script}=launch_failed")
                continue
            launched[script] = (process, time.monotonic())
            RUNNING_PROCESSES[script] = process

        log(
            f"STRATEGY_PROCESSES_LAUNCHED count={len(launched)}/"
            f"{len(STRATEGIES)}; all launched processes now run independently."
        )

        # All children are already running. Waiting in this loop does not
        # serialize their execution; each child continues scanning in parallel.
        for script, (process, launched_at) in launched.items():
            code = _wait_for_strategy(script, process, launched_at)
            if code and not STOP:
                failures.append(f"{script}={code}")
    finally:
        RUNNING_PROCESSES.clear()

    if STOP:
        return
    if failures:
        log("CYCLE completed with strategy errors: " + ", ".join(failures))
    elif len(available) < len({
        symbol.strip()
        for _, configured in STRATEGIES
        for symbol in configured.split(",")
        if symbol.strip()
    }):
        log("CYCLE completed with partial market coverage; failed datasets were skipped safely.")
    elif len(launched) != len(STRATEGIES):
        log(f"CYCLE completed with {len(launched)}/{len(STRATEGIES)} strategy processes launched.")
    else:
        log("CYCLE completed: all three independent strategy processes exited successfully")


def main():
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    log("VOROA_WORKER_MODE=independent_os_processes_v2")
    log("Voroa supervisor started: launching three independent strategy processes.")
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
