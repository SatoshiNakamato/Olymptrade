import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://api.twelvedata.com/time_series"
COMMODITIES_URL = "https://api.twelvedata.com/commodities"
CACHE_TTL_SECONDS = 45
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
            payload = json.load(handle)
        return payload if isinstance(payload, dict) and "values" in payload else None
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


def _request_json(url, params, attempts=3):
    """Retry transient rate-limit/server errors without exposing the API key."""
    last_message = "request failed"
    for attempt in range(attempts):
        try:
            response = requests.get(url, params=params, timeout=15)
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if response.status_code == 429 or response.status_code >= 500:
                last_message = (
                    payload.get("message")
                    or f"HTTP {response.status_code} from Twelve Data"
                )
                if attempt + 1 < attempts:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = min(8.0, max(1.0, float(retry_after))) if retry_after else 2 ** attempt
                    except ValueError:
                        delay = 2 ** attempt
                    time.sleep(delay)
                    continue
                raise MarketDataError(last_message)
            if response.status_code >= 400:
                # Do not include response.url: it contains the private API key.
                message = payload.get("message") or f"HTTP {response.status_code}"
                raise MarketDataError(f"Twelve Data returned HTTP {response.status_code}: {message}")
            if not isinstance(payload, dict):
                raise MarketDataError("Twelve Data returned an invalid JSON payload")
            if payload.get("status") == "error":
                raise MarketDataError(str(payload.get("message", "Twelve Data API error")))
            return payload
        except requests.RequestException as exc:
            last_message = type(exc).__name__
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
                continue
            raise MarketDataError(f"Twelve Data network error: {last_message}") from None
    raise MarketDataError(last_message)


def _silver_is_listed(api_key, symbol):
    """Confirm the exact spot-metal ticker against Twelve Data's commodity directory."""
    payload = _request_json(
        COMMODITIES_URL,
        {"symbol": symbol, "format": "JSON", "apikey": api_key},
        attempts=2,
    )
    rows = payload if isinstance(payload, list) else payload.get("data", payload.get("commodities", []))
    if isinstance(rows, dict):
        rows = [rows]
    return any(
        isinstance(row, dict)
        and str(row.get("symbol", "")).upper() == symbol.upper()
        for row in (rows or [])
    )


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
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "timezone": "UTC",
            "format": "JSON",
            "apikey": api_key,
        }
        try:
            payload = _request_json(BASE_URL, params)
        except MarketDataError as exc:
            # Some Twelve Data plans/data routes reject large silver intraday
            # requests with 404. Verify the ticker, then retry a smaller window.
            if symbol.upper() != "XAG/USD" or "404" not in str(exc):
                raise
            try:
                listed = _silver_is_listed(api_key, symbol)
            except MarketDataError as lookup_exc:
                raise MarketDataError(
                    f"{symbol}: time-series request returned 404 and commodity lookup failed: {lookup_exc}"
                ) from None
            if not listed:
                raise MarketDataError(
                    f"{symbol}: not present in Twelve Data's commodity directory; "
                    "check the API plan and supported silver symbol"
                ) from None
            params["outputsize"] = min(outputsize, 800)
            payload = _request_json(BASE_URL, params)

        if "values" not in payload:
            raise MarketDataError(
                f"{symbol}: Twelve Data response has no candle values "
                f"({payload.get('message', 'unknown response')})"
            )
        _save_cached(cache_path, payload)

    try:
        df = pd.DataFrame(payload["values"])
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataError(f"{symbol}: malformed candle response ({type(exc).__name__})") from None

    df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)

    minimum = (600 if symbol.upper() == "XAG/USD" else 1000) if interval == "5min" else 500
    if len(df) < minimum:
        raise MarketDataError(
            f"{symbol}: only {len(df)} {interval} candles returned; need at least {minimum}"
        )
    return df
