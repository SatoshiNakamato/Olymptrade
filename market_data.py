"""Biquote market data adapter (no API key).

Uses Biquote's public MT5-backed quote/candle service. Candle endpoints are
rolling windows (M1=300, M5=288, H1=168); open candles are excluded and
closed candles are returned oldest-first. No direct Yahoo Finance requests.
"""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://biquote.io"
CACHE_TTL_SECONDS = 20  # Keep scans fresh; each strategy process reuses recent per-market data.
DEFAULT_OUTPUTSIZE = 1600
CACHE_DIR = Path(tempfile.gettempdir()) / "olymptrade-market-cache"
SYMBOL_MAP = {
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/JPY": "USDJPY",
    "AUD/USD": "AUDUSD",
    "US500": "US500",
    "XAU/USD": "XAUUSD",
    "XAG/USD": "XAGUSD",
    "BTC/USD": "BTCUSD",
}
INTERVAL_MAP = {
    "1min": ("1m", 300, 100),
    "5min": ("5m", 288, 100),
    "1hour": ("1h", 168, 60),
}
INTERVAL_MINUTES = {"1min": 1, "5min": 5, "1hour": 60}
MAX_CANDLE_AGE_MULTIPLIER = 2


class MarketDataError(RuntimeError):
    pass


def _cache_path(symbol: str, interval: str, outputsize: int) -> Path:
    key = hashlib.sha256(
        f"biquote|{symbol}|{interval}|{outputsize}".encode("utf-8")
    ).hexdigest()
    return CACHE_DIR / f"{key}.json"


def _load_cached(path: Path):
    try:
        if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) and "timestamp" in payload else None
    except (OSError, ValueError):
        return None


def _save_cached(path: Path, payload):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError):
        pass


def _download_candles(symbol: str, interval: str) -> pd.DataFrame:
    api_symbol = SYMBOL_MAP.get(symbol.upper())
    if not api_symbol:
        raise MarketDataError(f"{symbol}: unsupported Biquote market symbol")
    api_interval, limit, minimum = INTERVAL_MAP[interval]
    url = f"{BASE_URL}/api/{api_symbol}/ohlc"
    last_error = "unknown response"

    for attempt in range(3):
        try:
            response = requests.get(
                url,
                params={"interval": api_interval, "limit": limit},
                headers={"Accept": "application/json", "User-Agent": "OlymptradeMarketAlerts/1.0"},
                timeout=8,
            )
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After")
                delay = min(2, int(retry_after)) if retry_after and retry_after.isdigit() else 2 ** attempt
                last_error = f"Biquote HTTP {response.status_code}"
                if attempt < 2:
                    time.sleep(delay)
                    continue
                raise MarketDataError(f"{symbol}: {last_error}; no signal generated")
            if response.status_code >= 400:
                raise MarketDataError(
                    f"{symbol}: Biquote HTTP {response.status_code}; no signal generated"
                )

            payload = response.json()
            bars = payload.get("bars") if isinstance(payload, dict) else None
            if not isinstance(bars, list) or not bars:
                raise MarketDataError(f"{symbol}: Biquote returned no candle bars")

            # Biquote sends newest-first and prepends the still-forming candle.
            closed = [bar for bar in bars if isinstance(bar, dict) and not bar.get("isOpen", False)]
            if not closed:
                raise MarketDataError(f"{symbol}: Biquote returned no completed candles")

            frame = pd.DataFrame(closed)
            required = ["openTime", "open", "high", "low", "close"]
            missing = [field for field in required if field not in frame.columns]
            if missing:
                raise MarketDataError(f"{symbol}: Biquote candles missing fields: {', '.join(missing)}")

            frame["datetime"] = pd.to_datetime(frame["openTime"], utc=True, errors="coerce")
            for field in ("open", "high", "low", "close"):
                frame[field] = pd.to_numeric(frame[field], errors="coerce")
            frame = frame.dropna(subset=["datetime", "open", "high", "low", "close"])
            frame = frame[[
                "datetime", "open", "high", "low", "close"
            ]].sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)

            valid = (
                (frame["high"] >= frame[["open", "close", "low"]].max(axis=1))
                & (frame["low"] <= frame[["open", "close", "high"]].min(axis=1))
                & (frame[["open", "high", "low", "close"]] > 0).all(axis=1)
            )
            frame = frame.loc[valid].reset_index(drop=True)
            if len(frame) < minimum:
                raise MarketDataError(
                    f"{symbol}: only {len(frame)} closed {api_interval} candles from Biquote; "
                    f"need at least {minimum}"
                )
            return frame.tail(limit).reset_index(drop=True)

        except requests.RequestException as exc:
            last_error = f"network error ({type(exc).__name__})"
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise MarketDataError(f"{symbol}: Biquote {last_error}; no signal generated") from None
        except (ValueError, TypeError, KeyError) as exc:
            raise MarketDataError(
                f"{symbol}: invalid Biquote response ({type(exc).__name__})"
            ) from None

    raise MarketDataError(f"{symbol}: {last_error}; no signal generated")


def _validate_freshness(df: pd.DataFrame, interval: str, now=None) -> None:
    """Reject stale or future-dated candles so strategies fail closed on bad feeds."""
    if df.empty:
        raise MarketDataError(f"no candles available for {interval}")
    latest_open = pd.Timestamp(df["datetime"].iloc[-1])
    if latest_open.tzinfo is None:
        latest_open = latest_open.tz_localize("UTC")
    else:
        latest_open = latest_open.tz_convert("UTC")
    current = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    else:
        current = current.tz_convert("UTC")
    latest_close = latest_open + pd.Timedelta(minutes=INTERVAL_MINUTES[interval])
    age = current - latest_close
    max_age = pd.Timedelta(
        minutes=INTERVAL_MINUTES[interval] * MAX_CANDLE_AGE_MULTIPLIER
    )
    if age < pd.Timedelta(seconds=-30):
        raise MarketDataError(
            f"latest {interval} candle is future-dated by {abs(age).total_seconds():.0f}s"
        )
    if age > max_age:
        raise MarketDataError(
            f"latest {interval} candle is stale ({age.total_seconds() / 60:.1f}m old; "
            f"maximum {max_age.total_seconds() / 60:.0f}m); no signal generated"
        )


def fetch_candles(
    symbol: str,
    outputsize: int = DEFAULT_OUTPUTSIZE,
    interval: str = "5min",
) -> pd.DataFrame:
    if interval not in INTERVAL_MAP:
        raise ValueError("interval must be 1min, 5min or 1hour")

    api_limit = INTERVAL_MAP[interval][1]
    requested = min(max(1, int(outputsize)), api_limit)
    cache_path = _cache_path(symbol, interval, requested)
    payload = _load_cached(cache_path)

    if payload is not None:
        try:
            df = pd.DataFrame(payload["timestamp"])
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
            for field in ("open", "high", "low", "close"):
                df[field] = pd.to_numeric(df[field], errors="coerce")
            df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
        except (KeyError, TypeError, ValueError):
            payload = None

    if payload is None:
        df = _download_candles(symbol, interval)
        serializable = {
            "timestamp": df.assign(
                datetime=df["datetime"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            ).to_dict(orient="records")
        }
        _save_cached(cache_path, serializable)

    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    df = df.tail(requested).reset_index(drop=True)
    minimum = INTERVAL_MAP[interval][2]
    if len(df) < minimum:
        raise MarketDataError(
            f"{symbol}: only {len(df)} closed {interval} candles available; need at least {minimum}"
        )
    try:
        _validate_freshness(df, interval)
    except MarketDataError as exc:
        raise MarketDataError(f"{symbol}: {exc}") from None
    return df
