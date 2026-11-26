"""One-market Voroa agent; runs all original strategy scripts in parallel."""
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from market_data import fetch_candles

ROOT = Path(os.environ["VOROA_ROOT"]).resolve()
SYMBOL = os.environ["VOROA_SYMBOL"]
STRATEGIES = ("main.py", "strategy2.py", "strategy3.py")
SCAN_INTERVAL_SECONDS = 20
STRATEGY_TIMEOUT_SECONDS = 240
STOP = False
CHILDREN = {}


def log(message):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{stamp}] [AGENT {SYMBOL}] {message}", flush=True)


def request_stop(signum, frame):
    global STOP
    STOP = True
    log(f"Stop signal {signum}; terminating active strategy evaluators.")
    for script, process in list(CHILDREN.items()):
        if process.poll() is None:
            try:
                process.terminate()
            except (OSError, ProcessLookupError):
                pass


def launch_strategy(script):
    path = ROOT / script
    if not path.is_file():
        log(f"ERROR {script}: strategy file missing: {path}")
        return None
    env = os.environ.copy()
    env["SYMBOLS"] = SYMBOL
    env["GLOBAL_ALERT_STATE_PATH"] = str(
        Path(os.environ["GLOBAL_ALERT_STATE_PATH"]).resolve()
    )
    env["PYTHONUNBUFFERED"] = "1"
    try:
        process = subprocess.Popen(
            [sys.executable, "-u", str(path)],
            cwd=str(Path.cwd()),
            env=env,
        )
    except Exception as exc:
        log(f"ERROR {script}: launch failed: {type(exc).__name__}: {exc}")
        return None
    log(f"STRATEGY_STARTED {script} pid={process.pid}")
    return process


def warm_symbol_data():
    """Prime the shared candle cache once per market before its three evaluators."""
    for interval in ("5min", "1hour"):
        try:
            fetch_candles(SYMBOL, interval=interval)
        except Exception as exc:
            log(f"DATA_PREFETCH_WARNING {interval}: {type(exc).__name__}: {exc}")
    # A failed prefetch does not disable the market; evaluators still retry normally.


def run_cycle():
    warm_symbol_data()
    failures = []
    launched = {}
    # Start all three strategy evaluators before waiting for any of them.
    for script in STRATEGIES:
        if STOP:
            break
        process = launch_strategy(script)
        if process is None:
            failures.append(f"{script}=launch_failed")
        else:
            launched[script] = (process, time.monotonic())
            CHILDREN[script] = process

    log(f"STRATEGIES_LAUNCHED {len(launched)}/3")
    for script, (process, started) in launched.items():
        remaining = max(0.1, STRATEGY_TIMEOUT_SECONDS - (time.monotonic() - started))
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            log(f"ERROR {script}: timed out; terminating")
            process.terminate()
            try:
                code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                code = process.wait()
        CHILDREN.pop(script, None)
        if code == 0:
            log(f"STRATEGY_FINISHED {script} exit_code=0")
        elif STOP:
            log(f"STRATEGY_STOPPED {script} exit_code={code}")
        else:
            log(f"STRATEGY_ERROR {script} exit_code={code}")
            failures.append(f"{script}={code}")
    if failures:
        log("CYCLE_ERRORS " + ", ".join(failures))
    else:
        log("CYCLE_COMPLETE all three strategy evaluators finished")


def main():
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    log("STARTED; isolated state directory=" + str(Path.cwd()))
    log("Original strategy logic/timeframes/filters are unchanged.")
    log("Alert-only; no broker orders are placed.")
    while not STOP:
        started = time.monotonic()
        run_cycle()
        if STOP:
            break
        delay = max(0.0, SCAN_INTERVAL_SECONDS - (time.monotonic() - started))
        log(f"NEXT_CYCLE_IN {delay:.1f}s")
        deadline = time.monotonic() + delay
        while not STOP and time.monotonic() < deadline:
            time.sleep(min(0.5, deadline - time.monotonic()))
    request_stop(signal.SIGTERM, None)
    for process in list(CHILDREN.values()):
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    log("STOPPED")


if __name__ == "__main__":
    main()
