"""Risk engine: tanking detection, Half-Kelly sizing, volatility calibration.

Capital preservation mandate:
- Tanking assets get ZERO allocation
- Position sizes scale asymptotically to $0 during high-ATR regimes
- Fractional Half-Kelly limits exposure based on historical win-rate
"""

import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from app.config import settings
from app.indicators import add_indicators


@dataclass
class AssetRiskProfile:
    ticker: str
    is_tanking: bool
    tanking_reason: Optional[str]
    win_rate_30d: float
    risk_reward_ratio: float
    kelly_fraction: float
    volatility_scalar: float
    optimal_position_pct: float
    optimal_position_usd: float
    atr_current: float
    atr_avg_30: float
    ema_20: float
    ema_50: float
    ema_200: float
    rsi: float
    current_price: float
    recommend_liquidate: bool


def compute_half_kelly(win_rate: float, risk_reward: float) -> float:
    """Fractional Half-Kelly: 0.5 * (W - ((1 - W) / R)).

    Returns 0 if negative (no edge).
    """
    if risk_reward <= 0:
        return 0.0
    kelly = win_rate - ((1 - win_rate) / risk_reward)
    half_kelly = 0.5 * kelly
    return max(0.0, half_kelly)


def compute_volatility_scalar(atr_current: float, atr_avg_30: float) -> float:
    """Scale position linearly toward 0 as ATR rises above the 30-day average.

    scalar = max(0, 1 - ((atr_current / atr_avg_30) - 1))
    When ATR == avg → 1.0 (full size)
    When ATR == 2x avg → 0.0 (zero size)
    When ATR > 2x avg → 0.0 (clamped)
    """
    if atr_avg_30 <= 0:
        return 0.0
    ratio = atr_current / atr_avg_30
    scalar = max(0.0, 1.0 - (ratio - 1.0))
    return min(1.0, scalar)


def detect_tanking(
    price: float, ema_20: float, ema_50: float, ema_200: float,
    ema_20_prev: float, ema_50_prev: float,
) -> tuple[bool, Optional[str]]:
    """Detect if an asset is in a 'tanking' phase.

    Conditions (any triggers tanking):
    1. Price below EMA 200
    2. EMA 20/50 bearish cross (20 crosses below 50)
    """
    reasons = []
    if price < ema_200:
        reasons.append("Price below 200 EMA")
    bearish_cross = ema_20_prev >= ema_50_prev and ema_20 < ema_50
    if bearish_cross:
        reasons.append("EMA 20/50 bearish cross")
    is_tanking = len(reasons) > 0
    return is_tanking, " + ".join(reasons) if reasons else None


def compute_30d_win_rate(df: pd.DataFrame) -> float:
    """Simulate the strategy over the last 30 bars and compute win rate."""
    if len(df) < 32:
        return 0.0
    recent = df.tail(31)
    wins = 0
    total = 0
    for i in range(1, len(recent)):
        row = recent.iloc[i]
        prev = recent.iloc[i - 1]
        if pd.isna(row.get("ema_20")) or pd.isna(row.get("ema_50")):
            continue
        golden = prev.get("ema_20", 0) <= prev.get("ema_50", 0) and row["ema_20"] > row["ema_50"]
        above_200 = row["close"] > row.get("ema_200", float("inf"))
        rsi_ok = 45 <= row.get("rsi", 0) <= 55
        if golden and above_200 and rsi_ok:
            total += 1
            stop = row["close"] - (settings.ATR_STOP_MULTIPLIER * row.get("atr", 0))
            target = row["close"] + (settings.RISK_REWARD_RATIO * (row["close"] - stop))
            future_prices = recent.iloc[i:]["close"].values
            for fp in future_prices:
                if fp >= target:
                    wins += 1
                    break
                if fp <= stop:
                    break
    return wins / total if total > 0 else 0.5


def evaluate_risk_profile(
    df: pd.DataFrame, ticker: str, available_capital: float,
) -> Optional[AssetRiskProfile]:
    """Full risk evaluation for an asset given its OHLCV DataFrame."""
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

    if pd.isna(atr) or pd.isna(rsi) or pd.isna(ema_200) or pd.isna(atr_avg_30):
        return None

    is_tanking, tanking_reason = detect_tanking(
        price, ema_20, ema_50, ema_200,
        row["ema_20_prev"], row["ema_50_prev"],
    )

    win_rate = compute_30d_win_rate(df)
    rr = settings.RISK_REWARD_RATIO

    kelly = compute_half_kelly(win_rate, rr)
    vol_scalar = compute_volatility_scalar(atr, atr_avg_30)

    if is_tanking:
        optimal_pct = 0.0
    else:
        optimal_pct = kelly * vol_scalar

    optimal_usd = optimal_pct * available_capital

    return AssetRiskProfile(
        ticker=ticker,
        is_tanking=is_tanking,
        tanking_reason=tanking_reason,
        win_rate_30d=round(win_rate, 4),
        risk_reward_ratio=rr,
        kelly_fraction=round(kelly, 6),
        volatility_scalar=round(vol_scalar, 6),
        optimal_position_pct=round(optimal_pct, 6),
        optimal_position_usd=round(optimal_usd, 2),
        atr_current=round(atr, 6),
        atr_avg_30=round(atr_avg_30, 6),
        ema_20=round(ema_20, 6),
        ema_50=round(ema_50, 6),
        ema_200=round(ema_200, 6),
        rsi=round(rsi, 2),
        current_price=round(price, 6),
        recommend_liquidate=is_tanking,
    )
