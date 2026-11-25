"""Cross-strategy email deduplication for the sequential alert worker."""
import json
import os
import tempfile
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
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(STATE_PATH.parent),
            prefix=".global_alert_state.", suffix=".tmp", delete=False
        ) as handle:
            json.dump(state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, STATE_PATH)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
