import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

import requests
import pandas as pd

BASE_URL = "https://api.twelvedata.com/time_series"
# All three strategy processes share this short-lived cache. The first strategy
# fetches each symbol; later strategies reuse the same fresh candle response.
CACHE_TTL_SECONDS = 15
DEFAULT_OUTPUTSIZE = 1600
CACHE_DIR = Path(tempfile.gettempdir()) / "olymptrade-market-cache"


class MarketDataError(RuntimeError):
    pass


def _cache_path(symbol: str, interval: str, outputsize: int) -> Path:
    key = hashlib.sha256(
        f"{symbol}|{interval}|{outputsize}".encode("utf-8")
    ).hexdigest()
    return CACHE_DIR / f"{key}.json"


def _load_cached(path: Path):
    try:
        if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
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
        # A cache write failure must never prevent a live market-data request.
        pass


def fetch_candles(
    symbol: str,
    outputsize: int = DEFAULT_OUTPUTSIZE,
    interval: str = "5min",
) -> pd.DataFrame:
    api_key = os.environ.get("TWELVEDATA_API_KEY")
    if not api_key:
        raise MarketDataError("Missing TWELVEDATA_API_KEY secret")
    if interval not in {"1min", "5min"}:
        raise ValueError("interval must be 1min or 5min")

    cache_path = _cache_path(symbol, interval, outputsize)
    payload = _load_cached(cache_path)
    if payload is None:
        try:
            response = requests.get(
                BASE_URL,
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "outputsize": outputsize,
                    "timezone": "UTC",
                    "format": "JSON",
                    "apikey": api_key,
                },
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise MarketDataError(f"{symbol}: market-data request failed: {exc}") from exc

        if payload.get("status") == "error" or "values" not in payload:
            raise MarketDataError(f"{symbol}: {payload.get('message', payload)}")
        _save_cached(cache_path, payload)

    df = pd.DataFrame(payload["values"])
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)

    minimum = 1000 if interval == "5min" else 500
    if len(df) < minimum:
        raise MarketDataError(
            f"{symbol}: only {len(df)} {interval} candles returned; need at least {minimum}"
        )
    return df
