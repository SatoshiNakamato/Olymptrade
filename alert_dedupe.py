"""Cross-strategy email deduplication for concurrent scanner workers."""
import fcntl
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_PATH = Path(os.environ.get("GLOBAL_ALERT_STATE_PATH", ".global_alert_state.json")).expanduser()
COOLDOWN = timedelta(minutes=20)


@contextmanager
def _file_lock(lock_path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _state_lock():
    """Serialize shared-state reads and atomic writes, never SMTP delivery."""
    with _file_lock(STATE_PATH.with_name(STATE_PATH.name + ".lock")):
        yield


@contextmanager
def _alert_lock(symbol, action):
    """Serialize only identical symbol/direction alerts across processes."""
    key = hashlib.sha256(f"{symbol}|{action}".encode("utf-8")).hexdigest()
    with _file_lock(STATE_PATH.with_name(f"{STATE_PATH.name}.{key}.lock")):
        yield


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
    """Send identical alerts once; allow unrelated symbol/direction emails in parallel.

    The per-alert lock prevents duplicate sends. The short global state lock is
    held only for checking/updating JSON, not while SMTP connects or sends.
    """
    with _alert_lock(symbol, action):
        check_time = datetime.now(timezone.utc)
        with _state_lock():
            if is_duplicate(symbol, action, check_time):
                print(
                    f"{strategy or 'Scanner'} duplicate suppressed at shared send lock: "
                    f"{symbol} {action}",
                    flush=True,
                )
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
        with _state_lock():
            record_sent(symbol, action, sent_at)
        print(
            f"[{sent_at.isoformat(timespec='milliseconds')}] "
            f"{label}ALERT_SEND_SUCCESS {symbol} {action} "
            f"smtp_seconds={elapsed:.2f}",
            flush=True,
        )
        return True
