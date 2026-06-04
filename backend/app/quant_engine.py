"""Quantitative analysis engine: EMA, RSI, ATR, and signal generation."""

import pandas as pd
import numpy as np
from typing import Optional

from app.models import SignalDirection
from app.config import settings


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


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(span=period, adjust=False).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all technical indicators to a candle dataframe."""
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


class SignalResult:
    def __init__(
        self,
        direction: Optional[SignalDirection],
        trigger_price: float,
        stop_loss: float,
        target_price: float,
        reason: str,
        risk_reward: float,
        atr_value: float,
        rsi_value: float,
        suppressed: bool = False,
    ):
        self.direction = direction
        self.trigger_price = trigger_price
        self.stop_loss = stop_loss
        self.target_price = target_price
        self.reason = reason
        self.risk_reward = risk_reward
        self.atr_value = atr_value
        self.rsi_value = rsi_value
        self.suppressed = suppressed


def evaluate_signals(df: pd.DataFrame) -> Optional[SignalResult]:
    """Evaluate the latest row for buy/sell signals with all guardrails."""
    if len(df) < 201:
        return None

    df = add_indicators(df)
    row = df.iloc[-1]
    prev = df.iloc[-2]

    price = row["close"]
    ema_20 = row["ema_20"]
    ema_50 = row["ema_50"]
    ema_200 = row["ema_200"]
    rsi = row["rsi"]
    atr = row["atr"]
    atr_avg_30 = row["atr_avg_30"]
    ema_20_prev = row["ema_20_prev"]
    ema_50_prev = row["ema_50_prev"]

    if pd.isna(atr) or pd.isna(rsi) or pd.isna(ema_200) or pd.isna(atr_avg_30):
        return None

    # Volatility filter: suppress if ATR > 2x 30-day average
    volatile = atr > settings.ATR_VOLATILITY_THRESHOLD * atr_avg_30

    # BUY signal logic
    golden_cross = ema_20_prev <= ema_50_prev and ema_20 > ema_50
    above_200 = price > ema_200
    rsi_buy_zone = 45 <= rsi <= 55

    if golden_cross and above_200 and rsi_buy_zone:
        stop_loss = price - (settings.ATR_STOP_MULTIPLIER * atr)
        risk = price - stop_loss
        reward = risk * settings.RISK_REWARD_RATIO
        target_price = price + reward
        rr = reward / risk if risk > 0 else 0

        return SignalResult(
            direction=SignalDirection.BUY,
            trigger_price=round(price, 6),
            stop_loss=round(stop_loss, 6),
            target_price=round(target_price, 6),
            reason="EMA 20/50 Golden Cross + Price above EMA 200 + RSI in stable zone",
            risk_reward=round(rr, 2),
            atr_value=round(atr, 6),
            rsi_value=round(rsi, 2),
            suppressed=volatile,
        )

    # SELL signal logic
    below_50 = price < ema_50
    rsi_overbought = rsi > 75

    if below_50 or rsi_overbought:
        stop_loss = price + (settings.ATR_STOP_MULTIPLIER * atr)
        risk = stop_loss - price
        reward = risk * settings.RISK_REWARD_RATIO
        target_price = price - reward
        rr = reward / risk if risk > 0 else 0
        reason_parts = []
        if below_50:
            reason_parts.append("Price below EMA 50")
        if rsi_overbought:
            reason_parts.append(f"RSI overbought ({rsi:.1f})")

        return SignalResult(
            direction=SignalDirection.SELL,
            trigger_price=round(price, 6),
            stop_loss=round(stop_loss, 6),
            target_price=round(target_price, 6),
            reason=" + ".join(reason_parts),
            risk_reward=round(rr, 2),
            atr_value=round(atr, 6),
            rsi_value=round(rsi, 2),
            suppressed=False,
        )

    return None
