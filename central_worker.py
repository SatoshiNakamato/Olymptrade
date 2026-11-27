"""Single-process central scanner for all markets and three unchanged strategies."""
import importlib
import json
import os
import signal
import sys
import time
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
MARKETS = ("XAU/USD", "XAG/USD", "US500")
STRATEGY_MODULES = ("main", "strategy2", "strategy3")
STATE_FILES = (".signal_state.json", ".strategy2_state.json", ".strategy3_state.json")
SCAN_INTERVAL_SECONDS = 60
IST = ZoneInfo("Asia/Kolkata")
LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")
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
    os.environ["SYMBOLS"] = ",".join(MARKETS)
    os.environ.setdefault("GLOBAL_ALERT_STATE_PATH", str(ROOT / ".global_alert_state.json"))

    market_data = importlib.import_module("market_data")
    original_fetch = market_data.fetch_candles
    shared_fetch, cache, counts = _shared_market_feed(original_fetch)
    modules = [importlib.import_module(name) for name in STRATEGY_MODULES]

    for module in modules:
        module.fetch_candles = shared_fetch

    log("CYCLE_STARTED markets=3 strategies=3 process_count=1")
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
    """True while either selected market's entry session is open."""
    utc_now = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    london = utc_now.astimezone(LONDON)
    new_york = utc_now.astimezone(NEW_YORK)
    london_open = london.weekday() < 5 and clock_time(8, 0) <= london.time().replace(tzinfo=None) < clock_time(17, 0)
    ny_minutes = new_york.hour * 60 + new_york.minute
    new_york_open = new_york.weekday() < 5 and 9 * 60 + 30 <= ny_minutes < 16 * 60
    return london_open or new_york_open


def _next_weekday_open(now_utc, zone, hour, minute):
    local = now_utc.astimezone(zone)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def seconds_until_next_start(now=None):
    utc_now = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    london_open = _next_weekday_open(utc_now, LONDON, 8, 0)
    new_york_open = _next_weekday_open(utc_now, NEW_YORK, 9, 30)
    next_open = min(london_open, new_york_open)
    return max(1.0, (next_open - utc_now).total_seconds())


def has_active_plans():
    """Keep checking only selected markets when an existing plan needs monitoring."""
    for filename in STATE_FILES:
        state = _read_json(ROOT / filename)
        for symbol in MARKETS:
            entry = state.get(symbol)
            if isinstance(entry, dict) and isinstance(entry.get("active_plan"), dict):
                return True
    return False

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
    log("CENTRAL_SCANNER_STARTED markets=3 strategies=3 processes=1 alert_only=true")
    log("MARKETS XAU/USD,XAG/USD,US500")
    log("OPERATING_SESSIONS XAU/XAG=08:00-17:00 Europe/London US500=09:30-16:00 America/New_York")
    log("STRATEGY_RULES_UNCHANGED shared_candles=true")

    migrate_market_state()
    next_scan = time.monotonic()
    was_active = False
    while not STOP:
        session_open = in_operating_window()
        active_plans = has_active_plans()
        if not session_open and not active_plans:
            if was_active:
                log("MARKET_SESSIONS_CLOSED; scanning paused until next London or New York open")
            was_active = False
            # Sleep while neither entry session is open and no selected-market plan
            # needs monitoring. Recheck periodically and remain responsive to shutdown.
            time.sleep(min(300.0, seconds_until_next_start()))
            next_scan = time.monotonic()
            continue

        if not was_active:
            if session_open:
                log("MARKET_SESSION_OPEN; scanning XAU/USD,XAG/USD,US500")
            else:
                log("ACTIVE_PLAN_MONITORING_ONLY; no new entries outside market sessions")
            was_active = True
            next_scan = time.monotonic()

        run_cycle()
        if STOP:
            break
        next_scan += SCAN_INTERVAL_SECONDS
        delay = next_scan - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            log(f"SCAN_CADENCE_OVERRUN seconds={-delay:.1f}; next cycle starts without overlap")

        if not in_operating_window():
            continue

    log("CENTRAL_SCANNER_STOPPED")


if __name__ == "__main__":
    main()
