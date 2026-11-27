"""Three-market concurrent scanner; one Python process, isolated strategy state per market."""
import importlib.util
import json
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from threading import Event, Lock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
MARKETS = ("XAU/USD", "XAG/USD", "US500")
STRATEGY_MODULES = ("main", "strategy2", "strategy3")
STATE_FILES = (".signal_state.json", ".strategy2_state.json", ".strategy3_state.json")
SCAN_INTERVAL_SECONDS = 60
MARKET_TASK_WARNING_SECONDS = 240
IST = ZoneInfo("Asia/Kolkata")
LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")
STOP = False
STOP_EVENT = Event()


def log(message):
    stamp = datetime.now(IST).isoformat(timespec="seconds")
    print(f"[{stamp}] {message}", flush=True)


def _slug(symbol):
    return symbol.lower().replace("/", "_")


def _market_state_dir(symbol):
    return ROOT / ".voroa_agents" / _slug(symbol)


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
    """Merge legacy per-market state into the central legacy files before seeding workers."""
    agent_root = ROOT / ".voroa_agents"
    if not agent_root.is_dir():
        return
    for filename in STATE_FILES:
        destination = ROOT / filename
        merged = _read_json(destination)
        changed = False
        for symbol in MARKETS:
            source = agent_root / _slug(symbol) / filename
            candidate = _read_json(source).get(symbol)
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
            log(f"LEGACY_STATE_MERGED {filename}")


def _prepare_market_state(symbol):
    """Seed a market's isolated files once; thereafter its files are authoritative."""
    directory = _market_state_dir(symbol)
    directory.mkdir(parents=True, exist_ok=True)
    for filename in STATE_FILES:
        local_path = directory / filename
        if local_path.exists():
            continue
        legacy_entry = _read_json(ROOT / filename).get(symbol)
        if isinstance(legacy_entry, dict):
            _atomic_json(local_path, {symbol: legacy_entry})


def _shared_market_feed(original_fetch):
    """Thread-safe per-cycle candle cache; one download per symbol/interval/size."""
    cache = {}
    key_locks = {}
    guard = Lock()
    counts = {"hits": 0, "misses": 0}

    def fetch(symbol, outputsize=1600, interval="5min"):
        key = (str(symbol).upper(), int(outputsize), str(interval))
        with guard:
            key_lock = key_locks.setdefault(key, Lock())
        with key_lock:
            with guard:
                frame = cache.get(key)
                if frame is not None:
                    counts["hits"] += 1
            if frame is None:
                frame = original_fetch(symbol, outputsize=outputsize, interval=interval)
                with guard:
                    cache[key] = frame
                    counts["misses"] += 1
            return frame.copy(deep=True) if hasattr(frame, "copy") else frame

    return fetch, cache, counts


def _load_strategy(name, symbol, shared_fetch):
    module_name = f"_market_{_slug(symbol)}_{name}"
    path = ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    module.fetch_candles = shared_fetch
    module.STATE_PATH = str(_market_state_dir(symbol) / STATE_FILES[STRATEGY_MODULES.index(name)])
    return module


def _run_market_task(symbol, shared_fetch):
    """Run all three strategies for one market; failures do not stop its sibling markets."""
    started = time.monotonic()
    log(f"MARKET_TASK_STARTED {symbol} strategies=3")
    failures = []
    for name in STRATEGY_MODULES:
        if STOP:
            break
        strategy_started = time.monotonic()
        try:
            module = _load_strategy(name, symbol, shared_fetch)
            log(f"STRATEGY_STARTED market={symbol} strategy={name}")
            module.main(symbols=[symbol])
            log(
                f"STRATEGY_FINISHED market={symbol} strategy={name} "
                f"duration={time.monotonic() - strategy_started:.1f}s"
            )
        except Exception as exc:
            failures.append((name, exc))
            log(
                f"STRATEGY_ERROR market={symbol} strategy={name} "
                f"{type(exc).__name__}: {exc}"
            )
    elapsed = time.monotonic() - started
    if failures:
        log(
            f"MARKET_TASK_FINISHED_WITH_ERRORS {symbol} duration={elapsed:.1f}s "
            + " | ".join(f"{name}:{type(exc).__name__}" for name, exc in failures)
        )
        return False
    log(f"MARKET_TASK_FINISHED {symbol} duration={elapsed:.1f}s")
    return True


def run_cycle():
    """Launch three concurrent market tasks in this single Python process."""
    started = time.monotonic()
    os.environ.setdefault("GLOBAL_ALERT_STATE_PATH", str(ROOT / ".global_alert_state.json"))
    market_data = __import__("market_data")
    shared_fetch, cache, counts = _shared_market_feed(market_data.fetch_candles)

    for symbol in MARKETS:
        _prepare_market_state(symbol)

    log("CYCLE_STARTED markets=3 concurrent_market_tasks=3 strategies_per_market=3 process_count=1")
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="market") as pool:
        futures = {pool.submit(_run_market_task, symbol, shared_fetch): symbol for symbol in MARKETS}
        done, pending = wait(futures, timeout=MARKET_TASK_WARNING_SECONDS)
        if pending:
            for future in pending:
                log(
                    f"MARKET_TASK_SLOW {futures[future]} "
                    f"elapsed_over_seconds={MARKET_TASK_WARNING_SECONDS}; task_kept_running"
                )
            # Python cannot safely kill a running thread. Keep the other tasks running,
            # allow bounded network/SMTP operations to finish, and never overlap cycles.
            wait(pending)
        failures = []
        for future, symbol in futures.items():
            try:
                if not future.result():
                    failures.append(symbol)
            except Exception as exc:
                failures.append(symbol)
                log(f"MARKET_TASK_CRASH {symbol} {type(exc).__name__}: {exc}")

    elapsed = time.monotonic() - started
    hits, misses = counts["hits"], counts["misses"]
    log(
        f"CYCLE_FINISHED duration={elapsed:.1f}s market_data_cache_hits={hits} "
        f"market_data_fetches={misses} cached_frames={len(cache)} "
        f"failed_markets={','.join(failures) if failures else 'none'}"
    )
    return not failures



def in_operating_window(now=None):
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
    return max(1.0, (min(london_open, new_york_open) - utc_now).total_seconds())


def has_active_plans():
    for symbol in MARKETS:
        for filename in STATE_FILES:
            entry = _read_json(_market_state_dir(symbol) / filename).get(symbol)
            if isinstance(entry, dict) and isinstance(entry.get("active_plan"), dict):
                return True
    return False


def request_stop(signum, frame):
    global STOP
    STOP = True
    STOP_EVENT.set()
    log(f"STOP_REQUESTED signal={signum} wake_requested=true")


def main():
    global STOP
    STOP = False
    STOP_EVENT.clear()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    os.environ.setdefault("GLOBAL_ALERT_STATE_PATH", str(ROOT / ".global_alert_state.json"))
    log("CENTRAL_SCANNER_STARTED markets=3 concurrent_tasks=3 strategies=3 processes=1 alert_only=true")
    log("MARKETS XAU/USD,XAG/USD,US500")
    log("OPERATING_SESSIONS XAU/XAG=08:00-17:00 Europe/London US500=09:30-16:00 America/New_York")
    log("TASK_MODEL one_process_three_market_threads isolated_strategy_state=true")
    log("STRATEGY_RULES_UNCHANGED shared_candles=true")

    migrate_market_state()
    for symbol in MARKETS:
        _prepare_market_state(symbol)
    next_scan = time.monotonic()
    was_active = False
    while not STOP:
        session_open = in_operating_window()
        active_plans = has_active_plans()
        if not session_open and not active_plans:
            if was_active:
                log("MARKET_SESSIONS_CLOSED; scanning paused until next London or New York open")
            was_active = False
            delay = min(300.0, seconds_until_next_start())
            next_open = datetime.now(timezone.utc) + timedelta(seconds=delay)
            log(
                f"IDLE_WAIT reason=no_open_sessions_no_active_plans "
                f"wait_seconds={int(delay)} next_check_utc={next_open.isoformat(timespec='seconds')}"
            )
            # Event.wait is interruptible: SIGTERM/SIGINT wakes the idle worker immediately.
            STOP_EVENT.wait(delay)
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
            # Keep the 60-second cadence while allowing graceful shutdown immediately.
            STOP_EVENT.wait(delay)
        else:
            log(f"SCAN_CADENCE_OVERRUN seconds={-delay:.1f}; next cycle starts without overlap")
    log("CENTRAL_SCANNER_STOPPED")


if __name__ == "__main__":
    main()
