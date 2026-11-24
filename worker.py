"""Persistent five-minute alert worker for Voroa.

Runs the three existing alert strategies in sequence. It never places trades.
Configure API/email credentials as environment variables in the hosting dashboard.
"""
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INTERVAL_SECONDS = max(60, int(os.getenv("SCAN_INTERVAL_SECONDS", "300")))
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


def run_cycle():
    failures = []
    for script, symbols in STRATEGIES:
        if STOP:
            break
        code = run_strategy(script, symbols)
        if code:
            failures.append(f"{script}={code}")
    if failures:
        log("CYCLE completed with errors: " + ", ".join(failures))
    else:
        log("CYCLE completed: all three strategies exited successfully")


def main():
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    log("Voroa alert worker started.")
    log(f"Scan interval: {INTERVAL_SECONDS} seconds.")
    log("Markets: EUR/USD, XAU/USD, XAG/USD, BTC/USD.")
    log("Mode: alert-only; no broker orders are placed.")

    if os.getenv("SEND_TEST_EMAIL_ON_START", "").strip().lower() in {"1", "true", "yes"}:
        log("Manual email test requested.")
        test_code = run_strategy("send_test_email.py", "")
        if test_code == 0:
            log("MANUAL EMAIL TEST: successful.")
        else:
            log(f"MANUAL EMAIL TEST: failed with exit code {test_code}.")

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
