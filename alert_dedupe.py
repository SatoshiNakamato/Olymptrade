"""Cross-strategy email deduplication for concurrent scanner workers."""
import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_PATH = Path(".global_alert_state.json")
COOLDOWN = timedelta(minutes=20)


@contextmanager
def _state_lock():
    """Serialize the duplicate-check -> email-send -> state-write transaction."""
    lock_path = STATE_PATH.with_name(STATE_PATH.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_state():
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def is_duplicate(symbol, action, now):
    state = _read_state()
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
    state = _read_state()
    state[symbol] = {
        "action": action,
        "sent_at": now.astimezone(timezone.utc).isoformat(),
    }
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


def send_if_not_duplicate(symbol, action, now, send, strategy=""):
    """Send once across concurrent workers; record only after successful SMTP send.

    Returns True when this call sent the alert, False when a duplicate was
    suppressed. SMTP errors propagate and leave the alert eligible for retry.
    """
    with _state_lock():
        if is_duplicate(symbol, action, now):
            print(f"{strategy or 'Scanner'} duplicate suppressed at shared send lock: {symbol} {action}", flush=True)
            return False
        label = f"{strategy} " if strategy else ""
        started_at = datetime.now(timezone.utc)
        started = time.monotonic()
        print(
            f"[{started_at.isoformat(timespec='milliseconds')}] "
            f"{label}ALERT_SEND_START {symbol} {action}",
            flush=True,
        )
        send()
        sent_at = datetime.now(timezone.utc)
        elapsed = time.monotonic() - started
        record_sent(symbol, action, now)
        print(
            f"[{sent_at.isoformat(timespec='milliseconds')}] "
            f"{label}ALERT_SEND_SUCCESS {symbol} {action} "
            f"smtp_seconds={elapsed:.2f}",
            flush=True,
        )
        return True
