"""Cross-strategy email deduplication for the sequential alert worker."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_PATH = Path(".global_alert_state.json")
COOLDOWN = timedelta(minutes=20)


def is_duplicate(symbol, action, now):
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    previous = state.get(symbol, {})
    if previous.get("action") != action:
        return False
    try:
        sent_at = datetime.fromisoformat(previous["sent_at"])
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
    except (KeyError, ValueError, TypeError):
        return False
    return now - sent_at < COOLDOWN


def record_sent(symbol, action, now):
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    state[symbol] = {"action": action, "sent_at": now.astimezone(timezone.utc).isoformat()}
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
