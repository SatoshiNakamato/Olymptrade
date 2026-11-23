import os
import requests
import pandas as pd

BASE_URL = "https://api.twelvedata.com/time_series"

class MarketDataError(RuntimeError):
    pass

def fetch_candles(symbol: str, outputsize: int = 3500, interval: str = "5min") -> pd.DataFrame:
    api_key = os.environ.get("TWELVEDATA_API_KEY")
    if not api_key:
        raise MarketDataError("Missing TWELVEDATA_API_KEY secret")
    if interval not in {"1min", "5min"}:
        raise ValueError("interval must be 1min or 5min")
    response = requests.get(BASE_URL, params={
        "symbol": symbol, "interval": interval, "outputsize": outputsize,
        "timezone": "UTC", "format": "JSON", "apikey": api_key,
    }, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") == "error" or "values" not in payload:
        raise MarketDataError(f"{symbol}: {payload.get('message', payload)}")
    df = pd.DataFrame(payload["values"])
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    minimum = 1000 if interval == "5min" else 500
    if len(df) < minimum:
        raise MarketDataError(f"{symbol}: only {len(df)} {interval} candles returned; need at least {minimum}")
    return df
