import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from threading import Lock

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_CACHE_PATH = Path(os.getenv("USD_CALENDAR_CACHE_PATH", ".usd_calendar_cache.json"))
CALENDAR_CACHE_TTL_SECONDS = 15 * 60
CALENDAR_STALE_MAX_SECONDS = 6 * 60 * 60
CALENDAR_LOCK = Lock()


def london_session_status(now_utc):
    london = now_utc.astimezone(ZoneInfo("Europe/London"))
    if london.weekday() >= 5:
        return False, "Weekend: London session is closed"
    if not (8 <= london.hour < 17):
        return False, "Outside London session (08:00–17:00 Europe/London)"
    return True, "Inside London session"


def new_york_session_status(now_utc):
    """Cash-session filter for US index CFDs; weekdays 09:30–16:00 New York time."""
    new_york = now_utc.astimezone(ZoneInfo("America/New_York"))
    if new_york.weekday() >= 5:
        return False, "Weekend: US cash session is closed"
    minutes = new_york.hour * 60 + new_york.minute
    if not (9 * 60 + 30 <= minutes < 16 * 60):
        return False, "Outside US cash session (09:30–16:00 America/New_York)"
    return True, "Inside US cash session"


def entry_session_status(symbol, now_utc):
    """Fail closed unless a selected market is inside its own entry session."""
    market = str(symbol).upper()
    if market == "US500":
        return new_york_session_status(now_utc)
    if market in {"XAU/USD", "XAG/USD"}:
        return london_session_status(now_utc)
    return False, f"Unsupported market: {market}"


def _load_calendar_cache():
    try:
        data = json.loads(CALENDAR_CACHE_PATH.read_text(encoding="utf-8"))
        fetched_at = float(data["fetched_at"])
        events = data["events"]
        if not isinstance(events, list):
            return None
        age = max(0, time.time() - fetched_at)
        return events, age
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_calendar_cache(events):
    temp = CALENDAR_CACHE_PATH.with_name(
        CALENDAR_CACHE_PATH.name + f".{os.getpid()}.tmp"
    )
    try:
        CALENDAR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(
            json.dumps({"fetched_at": time.time(), "events": events}),
            encoding="utf-8",
        )
        os.replace(temp, CALENDAR_CACHE_PATH)
    except (OSError, TypeError, ValueError):
        # A cache write failure must not invalidate a successfully fetched calendar.
        pass
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def _fetch_calendar():
    last_error = "calendar request failed"
    for attempt in range(3):
        try:
            response = requests.get(CALENDAR_URL, timeout=8)
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"calendar HTTP {response.status_code}"
                if attempt < 2:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = min(5.0, max(1.0, float(retry_after))) if retry_after else 2 ** attempt
                    except ValueError:
                        delay = 2 ** attempt
                    time.sleep(delay)
                    continue
                raise RuntimeError(last_error)
            response.raise_for_status()
            events = response.json()
            if not isinstance(events, list):
                raise ValueError("calendar response was not a list")
            _save_calendar_cache(events)
            return events, "live"
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = str(exc)
            if attempt < 2 and not isinstance(exc, RuntimeError):
                time.sleep(2 ** attempt)
                continue
            break

    cached = _load_calendar_cache()
    if cached and cached[1] <= CALENDAR_STALE_MAX_SECONDS:
        return cached[0], f"cached ({int(cached[1] // 60)} minutes old; live fetch failed: {last_error})"
    raise RuntimeError(
        f"cannot verify USD news calendar and no cache younger than "
        f"{CALENDAR_STALE_MAX_SECONDS // 3600} hours is available ({last_error})"
    )


def _usd_high_impact_news_status_locked(now_utc):
    """Fail closed if no current or sufficiently recent USD calendar is available."""
    try:
        cached = _load_calendar_cache()
        if cached and cached[1] <= CALENDAR_CACHE_TTL_SECONDS:
            events, source = cached[0], "cache"
        else:
            events, source = _fetch_calendar()

        for event in events:
            if not isinstance(event, dict):
                continue
            if str(event.get("country", "")).upper() != "USD":
                continue
            if str(event.get("impact", "")).lower() != "high":
                continue
            raw = event.get("date")
            if not raw:
                continue
            event_time = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            event_time = event_time.astimezone(timezone.utc)
            if now_utc - timedelta(minutes=15) <= event_time <= now_utc + timedelta(minutes=15):
                return False, (
                    f'USD high-impact news blackout: {event.get("title", "event")} '
                    f'at {event_time.isoformat()}'
                )
        return True, f"No high-impact USD event within ±15 minutes (calendar: {source})"
    except Exception as exc:
        return False, f"Cannot verify USD news calendar; affected alerts blocked ({exc})"


def usd_high_impact_news_status(now_utc):
    """Serialize calendar refreshes shared by the three concurrent market tasks."""
    with CALENDAR_LOCK:
        return _usd_high_impact_news_status_locked(now_utc)
