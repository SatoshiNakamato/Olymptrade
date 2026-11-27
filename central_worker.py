"""Single-process central scanner for all markets and three unchanged strategies."""
import importlib
import json
import os
import signal
import sys
import time
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
MARKETS = (
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
    "XAU/USD", "XAG/USD", "BTC/USD", "US500",
)
STRATEGY_MODULES = ("main", "strategy2", "strategy3")
STATE_FILES = (".signal_state.json", ".strategy2_state.json", ".strategy3_state.json")
SCAN_INTERVAL_SECONDS = 60
IST = ZoneInfo("Asia/Kolkata")
STOP = False


def log(message):
    stamp = datetime.now(IST).isoformat(timespec="seconds")
    print(f"[{stamp}] {message}", flush=True)


def _slug(symbol):
    return symbol.lower().replace("/", "_")


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


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


def _entry_time(entry):
    value = entry.get("sent_at") if isinstance(entry, dict) else None
    if not value:
        return datetime.min.replace(tzinfo=IST)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=IST)


def migrate_market_state():
    """Merge per-market state created by the previous worker into central files."""
    agent_root = ROOT / ".voroa_agents"
    if not agent_root.is_dir():
        return
    for filename in STATE_FILES:
        destination = ROOT / filename
        merged = _read_json(destination)
        changed = False
        for symbol in MARKETS:
            source = agent_root / _slug(symbol) / filename
            old = _read_json(source)
            candidate = old.get(symbol)
            if not isinstance(candidate, dict):
                continue
            current = merged.get(symbol)
            if not isinstance(current, dict) or _entry_time(candidate) >= _entry_time(current):
                combined = dict(current or {})
                combined.update(candidate)
                if not combined.get("active_plan") and (current or {}).get("active_plan"):
                    combined["active_plan"] = current["active_plan"]
                merged[symbol] = combined
                changed = True
        if changed:
            _atomic_json(destination, merged)
            log(f"STATE_MIGRATED {filename} from previous per-market worker")


def _shared_market_feed(original_fetch):
    """Return one per-cycle candle cache shared by all three strategy modules."""
    cache = {}
    downloads = {"hits": 0, "misses": 0}

    def fetch(symbol, outputsize=1600, interval="5min"):
        key = (str(symbol).upper(), int(outputsize), str(interval))
        if key not in cache:
            cache[key] = original_fetch(symbol, outputsize=outputsize, interval=interval)
            downloads["misses"] += 1
        else:
            downloads["hits"] += 1
        frame = cache[key]
        return frame.copy(deep=True) if hasattr(frame, "copy") else frame

    return fetch, cache, downloads


def run_cycle():
    """Run all strategies sequentially in this process with shared candle data."""
    started = time.monotonic()
    os.environ.setdefault("SYMBOLS", ",".join(MARKETS))
    os.environ.setdefault("GLOBAL_ALERT_STATE_PATH", str(ROOT / ".global_alert_state.json"))

    market_data = importlib.import_module("market_data")
    original_fetch = market_data.fetch_candles
    shared_fetch, cache, counts = _shared_market_feed(original_fetch)
    modules = [importlib.import_module(name) for name in STRATEGY_MODULES]

    for module in modules:
        module.fetch_candles = shared_fetch

    log("CYCLE_STARTED markets=8 strategies=3 process_count=1")
    failures = []
    for name, module in zip(STRATEGY_MODULES, modules):
        if STOP:
            break
        strategy_started = time.monotonic()
        log(f"STRATEGY_STARTED {name}.py")
        try:
            module.main()
        except Exception as exc:
            failures.append((name, exc))
            log(f"STRATEGY_ERROR {name}.py {type(exc).__name__}: {exc}")
        else:
            log(f"STRATEGY_FINISHED {name}.py duration={time.monotonic() - strategy_started:.1f}s")

    elapsed = time.monotonic() - started
    log(
        f"CYCLE_FINISHED duration={elapsed:.1f}s "
        f"market_data_cache_hits={counts['hits']} "
        f"market_data_fetches={counts['misses']} cached_frames={len(cache)}"
    )
    if failures:
        log("CYCLE_ERRORS " + " | ".join(f"{name}: {type(exc).__name__}" for name, exc in failures))
    return not failures


def in_operating_window(now=None):
    local = now.astimezone(IST) if now else datetime.now(IST)
    return clock_time(0, 0) <= local.time().replace(tzinfo=None) < clock_time(20, 0)


def seconds_until_next_start(now=None):
    local = (now.astimezone(IST) if now else datetime.now(IST))
    next_start = (local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(1.0, (next_start - local).total_seconds())


def request_stop(signum, frame):
    global STOP
    STOP = True
    log(f"STOP_REQUESTED signal={signum}")


def main():
    global STOP
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    os.environ.setdefault("SYMBOLS", ",".join(MARKETS))
    os.environ.setdefault("GLOBAL_ALERT_STATE_PATH", str(ROOT / ".global_alert_state.json"))
    log("CENTRAL_SCANNER_STARTED markets=8 strategies=3 processes=1 alert_only=true")
    log("OPERATING_WINDOW 00:00-20:00 Asia/Kolkata")
    log("STRATEGY_RULES_UNCHANGED shared_candles=true")

    migrate_market_state()
    next_scan = time.monotonic()
    was_active = False
    while not STOP:
        if not in_operating_window():
            if was_active:
                log("OPERATING_WINDOW_CLOSED; scanning paused until 00:00 IST")
            was_active = False
            time.sleep(min(30.0, seconds_until_next_start()))
            next_scan = time.monotonic()
            continue

        if not was_active:
            log("OPERATING_WINDOW_OPEN; scanning all markets")
            was_active = True
            next_scan = time.monotonic()

        run_cycle()
        if STOP:
            break
        next_scan += SCAN_INTERVAL_SECONDS
        delay = max(0.0, next_scan - time.monotonic())
        if delay:
            time.sleep(delay)
        else:
            log(f"SCAN_CADENCE_OVERRUN seconds={-delay:.1f}; next cycle starts without overlap")

        if not in_operating_window():
            continue

    log("CENTRAL_SCANNER_STOPPED")


if __name__ == "__main__":
    main()
