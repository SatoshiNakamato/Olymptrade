import pandas as pd

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]
    out["ema20"] = close.ewm(span=20, adjust=False).mean()
    out["ema50"] = close.ewm(span=50, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    out["rsi"] = 100 - (100 / (1 + rs))
    fast = close.ewm(span=12, adjust=False).mean()
    slow = close.ewm(span=26, adjust=False).mean()
    out["macd"] = fast - slow
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    prev = out["close"].shift(1)
    tr = pd.concat([(out["high"]-out["low"]), (out["high"]-prev).abs(),
                    (out["low"]-prev).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(14).mean()
    return out
