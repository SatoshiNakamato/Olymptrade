"""One-market Voroa agent; runs all original strategies with bounded memory."""
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("VOROA_ROOT", Path(__file__).resolve().parent)).resolve()
SYMBOL = os.environ.get("VOROA_SYMBOL", "TEST/MARKET")
STRATEGIES = ("main.py", "strategy2.py", "strategy3.py")
SCAN_INTERVAL_SECONDS = 60
STRATEGY_TIMEOUT_SECONDS = 240
STOP = False
CHILDREN = {}


def log(message):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{stamp}] [AGENT {SYMBOL}] {message}", flush=True)


def request_stop(signum, frame):
    global STOP
    STOP = True
    log(f"Stop signal {signum}; terminating active strategy evaluator.")
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


def run_cycle():
    """Run all three strategy programs one at a time for this market."""
    cycle_started = time.monotonic()
    failures = []
    log("CYCLE_STARTED")
    for script in STRATEGIES:
        if STOP:
            break

        process = launch_strategy(script)
        if process is None:
            failures.append(f"{script}=launch_failed")
            continue

        CHILDREN[script] = process
        strategy_started = time.monotonic()
        try:
            code = process.wait(timeout=STRATEGY_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            log(f"ERROR {script}: timed out after {STRATEGY_TIMEOUT_SECONDS}s; terminating")
            process.terminate()
            try:
                code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                code = process.wait()
        finally:
            CHILDREN.pop(script, None)

        elapsed = time.monotonic() - strategy_started
        if code == 0:
            log(f"STRATEGY_FINISHED {script} exit_code=0 duration={elapsed:.1f}s")
        elif STOP:
            log(f"STRATEGY_STOPPED {script} exit_code={code} duration={elapsed:.1f}s")
        else:
            log(f"STRATEGY_ERROR {script} exit_code={code} duration={elapsed:.1f}s")
            failures.append(f"{script}={code}")

    elapsed = time.monotonic() - cycle_started
    if STOP:
        log(f"CYCLE_INTERRUPTED duration={elapsed:.1f}s")
    elif failures:
        log(f"CYCLE_FINISHED_WITH_ERRORS duration={elapsed:.1f}s: " + ", ".join(failures))
    else:
        log(f"CYCLE_COMPLETE all_three_strategies_finished duration={elapsed:.1f}s")


def main():
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    log("STARTED; isolated state directory=" + str(Path.cwd()))
    log("Original strategy logic/timeframes/filters are unchanged.")
    log("Strategies execute sequentially per market; all eight markets remain concurrent.")
    log("Alert-only; no broker orders are placed.")

    while not STOP:
        started = time.monotonic()
        run_cycle()
        if STOP:
            break
        elapsed = time.monotonic() - started
        delay = max(0.0, SCAN_INTERVAL_SECONDS - elapsed)
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
