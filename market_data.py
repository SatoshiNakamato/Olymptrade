"""No-key Yahoo Finance market data adapter.

Uses Yahoo Finance's public chart endpoint; no Twelve Data API key is required.
Yahoo's endpoint is unofficial and may throttle requests, so failures are surfaced
and callers must not create signals from missing or incomplete candles.
"""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

import pandas as pd
import requests

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
CACHE_TTL_SECONDS = 45
DEFAULT_OUTPUTSIZE = 1600
CACHE_DIR = Path(tempfile.gettempdir()) / "olymptrade-market-cache"
SYMBOL_MAP = {
    "EUR/USD": "EURUSD=X",
    # Yahoo spot aliases return 404; use liquid continuous futures as explicit proxies.
    "XAU/USD": "GC=F",  # COMEX gold futures, not spot XAU/USD
    "XAG/USD": "SI=F",  # COMEX silver futures, not spot XAG/USD
    "BTC/USD": "BTC-USD",
}


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


def _download_chart(symbol: str, interval: str):
    ticker = SYMBOL_MAP.get(symbol.upper())
    if not ticker:
        raise MarketDataError(f"{symbol}: unsupported market symbol")

    yahoo_interval = {"1min": "1m", "5min": "5m"}[interval]
    # Yahoo permits a shorter lookback for one-minute candles.
    period = "7d" if interval == "1min" else "60d"
    url = CHART_URL.format(ticker=ticker)
    params = {"range": period, "interval": yahoo_interval, "events": "history"}
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MarketAlertBot/1.0)",
        "Accept": "application/json",
    }

    last_error = "unknown response"
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=20)
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"Yahoo Finance HTTP {response.status_code}"
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise MarketDataError(f"{symbol}: {last_error}; no signal generated")
            if response.status_code >= 400:
                raise MarketDataError(
                    f"{symbol}: Yahoo Finance HTTP {response.status_code}; no signal generated"
                )
            payload = response.json()
            chart = payload.get("chart", {})
            if chart.get("error"):
                message = chart["error"].get("description") or "chart endpoint error"
                raise MarketDataError(f"{symbol}: Yahoo Finance: {message}")
            results = chart.get("result") or []
            if not results:
                raise MarketDataError(f"{symbol}: Yahoo Finance returned no candles")
            result = results[0]
            quote_list = result.get("indicators", {}).get("quote", [])
            if not quote_list:
                raise MarketDataError(f"{symbol}: Yahoo Finance returned no OHLC data")
            quote = quote_list[0]
            timestamps = result.get("timestamp") or []
            frame = pd.DataFrame({
                "datetime": pd.to_datetime(timestamps, unit="s", utc=True),
                "open": quote.get("open", []),
                "high": quote.get("high", []),
                "low": quote.get("low", []),
                "close": quote.get("close", []),
            })
            frame = frame.dropna(subset=["datetime", "open", "high", "low", "close"])
            frame = frame.sort_values("datetime").drop_duplicates("datetime")
            return frame.tail(max(1, int(DEFAULT_OUTPUTSIZE if interval == "5min" else 7000)))
        except requests.RequestException as exc:
            last_error = type(exc).__name__
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise MarketDataError(
                f"{symbol}: Yahoo Finance network error ({last_error}); no signal generated"
            ) from None
        except (ValueError, TypeError, KeyError) as exc:
            raise MarketDataError(
                f"{symbol}: invalid Yahoo Finance response ({type(exc).__name__})"
            ) from None
    raise MarketDataError(f"{symbol}: {last_error}; no signal generated")


def fetch_candles(
    symbol: str,
    outputsize: int = DEFAULT_OUTPUTSIZE,
    interval: str = "5min",
) -> pd.DataFrame:
    if interval not in {"1min", "5min"}:
        raise ValueError("interval must be 1min or 5min")

    cache_path = _cache_path(symbol, interval, outputsize)
    payload = _load_cached(cache_path)
    if payload is not None:
        try:
            df = pd.DataFrame(payload["timestamp"])
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
        except (KeyError, TypeError, ValueError):
            payload = None

    if payload is None:
        df = _download_chart(symbol, interval)
        if df.empty:
            raise MarketDataError(f"{symbol}: Yahoo Finance returned no valid candles")
        serializable = {
            "timestamp": df.assign(
                datetime=df["datetime"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            ).to_dict(orient="records")
        }
        _save_cached(cache_path, serializable)

    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    df = df.tail(int(outputsize)).reset_index(drop=True)
    minimum = 600 if symbol.upper() == "XAG/USD" and interval == "5min" else (
        1000 if interval == "5min" else 500
    )
    if len(df) < minimum:
        raise MarketDataError(
            f"{symbol}: only {len(df)} {interval} candles returned; need at least {minimum}"
        )
    return df
