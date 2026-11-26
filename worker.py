"""Voroa supervisor: one isolated, persistent agent per market.

Each market agent runs all three existing strategy evaluators independently.
Strategy rules remain in main.py, strategy2.py, strategy3.py and signals.py.
This service sends alerts only; it never places broker orders.
"""
import json
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
AGENT_DIR = ROOT / ".voroa_agents"
GLOBAL_ALERT_STATE = ROOT / ".global_alert_state.json"
MARKETS = (
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
    "XAU/USD", "XAG/USD", "BTC/USD", "US500",
)
DATA_INTERVALS = ("5min", "1hour")
REQUIRED_INTERVALS = ("5min", "1hour")
AGENT_RESTART_SECONDS = 5
STOP = False
RUNNING_AGENTS = {}
STATE_FILES = (
    ".signal_state.json",
    ".strategy2_state.json",
    ".strategy3_state.json",
)


def log(message):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{stamp}] {message}", flush=True)


def _slug(symbol):
    return symbol.lower().replace("/", "_")


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def prepare_agent_state(symbol):
    """Migrate the old shared per-strategy state into isolated per-market files."""
    state_dir = AGENT_DIR / _slug(symbol)
    state_dir.mkdir(parents=True, exist_ok=True)
    for filename in STATE_FILES:
        destination = state_dir / filename
        if destination.exists():
            continue
        source = ROOT / filename
        try:
            old_state = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(old_state, dict) and symbol in old_state:
            _atomic_json(destination, {symbol: old_state[symbol]})
            log(f"STATE_MIGRATED {symbol} {filename}")
    return state_dir


def request_stop(signum, frame):
    global STOP
    STOP = True
    log(f"Received stop signal {signum}; stopping all market agents.")
    for symbol, process in list(RUNNING_AGENTS.items()):
        try:
            if process.poll() is None:
                log(f"AGENT_STOP_REQUEST {symbol} pid={process.pid}")
                process.terminate()
        except (OSError, ProcessLookupError):
            pass


def warm_market_data():
    jobs = [(symbol, interval) for symbol in MARKETS for interval in DATA_INTERVALS]
    succeeded = set()
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
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
                log(f"DATA_PREFETCH_ERROR {symbol} {interval}: {type(exc).__name__}: {exc}")
    ready = {
        symbol for symbol in MARKETS
        if all((symbol, interval) in succeeded for interval in REQUIRED_INTERVALS)
    }
    log(
        f"DATA_PREFETCH {len(succeeded)}/{len(jobs)} datasets ready; "
        f"{len(ready)}/{len(MARKETS)} markets warm. All agents will start regardless."
    )
    return ready


def launch_agent(symbol):
    state_dir = prepare_agent_state(symbol)
    path = ROOT / "market_agent.py"
    env = os.environ.copy()
    env.update({
        "VOROA_ROOT": str(ROOT),
        "VOROA_SYMBOL": symbol,
        "GLOBAL_ALERT_STATE_PATH": str(GLOBAL_ALERT_STATE),
        "PYTHONUNBUFFERED": "1",
    })
    try:
        process = subprocess.Popen(
            [sys.executable, "-u", str(path)],
            cwd=str(state_dir),
            env=env,
        )
    except Exception as exc:
        log(f"AGENT_LAUNCH_ERROR {symbol}: {type(exc).__name__}: {exc}")
        return None
    log(f"MARKET_AGENT_STARTED {symbol} pid={process.pid} state_dir={state_dir}")
    return process


def main():
    global STOP
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    log("VOROA_WORKER_MODE=eight_independent_market_agents_v1")
    log("Markets: " + ", ".join(MARKETS))
    log("Each market agent runs Strategy 1, Strategy 2 and Strategy 3 concurrently.")
    log("Mode: alert-only; no broker orders are placed.")
    warm_market_data()

    next_restart = {symbol: 0.0 for symbol in MARKETS}
    for symbol in MARKETS:
        if STOP:
            break
        process = launch_agent(symbol)
        if process is not None:
            RUNNING_AGENTS[symbol] = process
        else:
            next_restart[symbol] = time.monotonic() + AGENT_RESTART_SECONDS

    log(f"MARKET_AGENTS_LAUNCHED count={len(RUNNING_AGENTS)}/{len(MARKETS)}")
    while not STOP:
        now = time.monotonic()
        for symbol in MARKETS:
            if STOP:
                break
            process = RUNNING_AGENTS.get(symbol)
            if process is not None and process.poll() is not None:
                code = process.returncode
                log(f"MARKET_AGENT_EXITED {symbol} exit_code={code}; restart scheduled")
                RUNNING_AGENTS.pop(symbol, None)
                next_restart[symbol] = now + AGENT_RESTART_SECONDS
            if process is None and now >= next_restart[symbol]:
                restarted = launch_agent(symbol)
                if restarted is not None:
                    RUNNING_AGENTS[symbol] = restarted
                else:
                    next_restart[symbol] = time.monotonic() + AGENT_RESTART_SECONDS
        time.sleep(1)

    for symbol, process in list(RUNNING_AGENTS.items()):
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    RUNNING_AGENTS.clear()
    log("Voroa eight-market supervisor stopped.")


if __name__ == "__main__":
    main()
