"""Technical indicators: EMA, RSI, ATR."""

import pandas as pd


def compute_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(span=period, adjust=False).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_20"] = compute_ema(df["close"], 20)
    df["ema_50"] = compute_ema(df["close"], 50)
    df["ema_200"] = compute_ema(df["close"], 200)
    df["rsi"] = compute_rsi(df["close"], 14)
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], 14)
    df["atr_avg_30"] = df["atr"].rolling(window=30).mean()
    df["ema_20_prev"] = df["ema_20"].shift(1)
    df["ema_50_prev"] = df["ema_50"].shift(1)
    return df
