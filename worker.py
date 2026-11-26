"""Voroa supervisor: one isolated, persistent agent per market.

Each market agent evaluates all three original strategies in sequence. The eight
markets still run concurrently, but only one strategy subprocess per market is
allowed at a time to keep peak memory bounded on small background-worker plans.
Strategy rules remain in main.py, strategy2.py, strategy3.py and signals.py.
This service sends alerts only; it never places broker orders.
"""
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENT_DIR = ROOT / ".voroa_agents"
GLOBAL_ALERT_STATE = ROOT / ".global_alert_state.json"
MARKETS = (
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
    "XAU/USD", "XAG/USD", "BTC/USD", "US500",
)
AGENT_RESTART_SECONDS = 5
HEARTBEAT_SECONDS = 60
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


def launch_all_agents():
    """Launch all eight market agents without waiting for any one to finish."""
    launched = {}
    for symbol in MARKETS:
        if STOP:
            break
        process = launch_agent(symbol)
        if process is not None:
            launched[symbol] = process
    return launched


def run_startup_email_test():
    """Preserve the explicit, opt-in SMTP test without running a market test alert."""
    requested = os.getenv("SEND_TEST_EMAIL_ON_START", "").strip().lower()
    if requested not in {"1", "true", "yes"}:
        return
    path = ROOT / "send_test_email.py"
    if not path.is_file():
        log("STARTUP EMAIL TEST skipped: send_test_email.py is missing.")
        return
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    log("Explicit startup email test requested.")
    try:
        result = subprocess.run(
            [sys.executable, "-u", str(path)],
            cwd=str(ROOT),
            env=env,
            check=False,
            timeout=45,
        )
        log(f"STARTUP EMAIL TEST exit_code={result.returncode}")
    except subprocess.TimeoutExpired:
        log("STARTUP EMAIL TEST timed out after 45 seconds.")
    except Exception as exc:
        log(f"STARTUP EMAIL TEST error: {type(exc).__name__}: {exc}")


def main():
    global STOP
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    log("VOROA_WORKER_MODE=eight_independent_market_agents_memory_bounded_v2")
    log("Markets: " + ", ".join(MARKETS))
    log("Each market runs Strategy 1, Strategy 2 and Strategy 3 sequentially.")
    log("All eight markets run concurrently; maximum strategy subprocesses=8.")
    log("Mode: alert-only; no broker orders are placed.")
    run_startup_email_test()

    next_restart = {symbol: 0.0 for symbol in MARKETS}
    RUNNING_AGENTS.update(launch_all_agents())
    for symbol in MARKETS:
        if symbol not in RUNNING_AGENTS:
            next_restart[symbol] = time.monotonic() + AGENT_RESTART_SECONDS

    log(f"MARKET_AGENTS_LAUNCHED count={len(RUNNING_AGENTS)}/{len(MARKETS)}")
    next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
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

        if now >= next_heartbeat:
            active = sum(1 for process in RUNNING_AGENTS.values() if process.poll() is None)
            log(f"SUPERVISOR_HEARTBEAT agents_running={active}/{len(MARKETS)}")
            next_heartbeat = now + HEARTBEAT_SECONDS
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
