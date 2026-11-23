import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def london_session_status(now_utc):
    london = now_utc.astimezone(ZoneInfo("Europe/London"))
    if london.weekday() >= 5:
        return False, "Weekend: London session is closed"
    if not (8 <= london.hour < 17):
        return False, "Outside London session (08:00–17:00 Europe/London)"
    return True, "Inside London session"


def usd_high_impact_news_status(now_utc):
    """Fail closed: no XAU/USD alerts if the high-impact USD calendar cannot be checked."""
    try:
        response = requests.get(CALENDAR_URL, timeout=12)
        response.raise_for_status()
        events = response.json()
        for event in events:
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
                return False, f'USD high-impact news blackout: {event.get("title", "event")} at {event_time.isoformat()}'
        return True, "No high-impact USD event within ±15 minutes"
    except Exception as exc:
        return False, f"Cannot verify USD news calendar; XAU/USD alerts blocked ({exc})"
