import os
import requests
import pandas as pd

BASE_URL = "https://api.twelvedata.com/time_series"

class MarketDataError(RuntimeError):
    pass

def fetch_candles(symbol: str, outputsize: int = 3500) -> pd.DataFrame:
    api_key = os.environ.get("TWELVEDATA_API_KEY")
    if not api_key:
        raise MarketDataError("Missing TWELVEDATA_API_KEY secret")
    response = requests.get(BASE_URL, params={
        "symbol": symbol, "interval": "5min", "outputsize": outputsize,
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
    if len(df) < 1000:
        raise MarketDataError(f"{symbol}: only {len(df)} candles returned; need more history")
    return df
