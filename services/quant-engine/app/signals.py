"""Signal generation with risk-adjusted sizing."""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from app.config import settings
from app.indicators import add_indicators
from app.risk_engine import (
    detect_tanking,
    compute_half_kelly,
    compute_volatility_scalar,
    compute_30d_win_rate,
)


@dataclass
class SignalResult:
    direction: Optional[str]
    status: str  # "Healthy Trend" | "Tanking" | "Suppressed"
    trigger_price: float
    stop_loss: float
    target_price: float
    reason: str
    risk_reward: float
    atr_value: float
    rsi_value: float
    suppressed: bool
    kelly_pct: float
    optimal_size_usd: float
    volatility_scalar: float


def evaluate_signals(
    df: pd.DataFrame, available_capital: float = 10_000.0,
) -> Optional[SignalResult]:
    if len(df) < 201:
        return None

    df = add_indicators(df)
    row = df.iloc[-1]

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

    is_tanking, tanking_reason = detect_tanking(
        price, ema_20, ema_50, ema_200, ema_20_prev, ema_50_prev,
    )
    volatile = atr > settings.ATR_VOLATILITY_THRESHOLD * atr_avg_30

    win_rate = compute_30d_win_rate(df)
    rr = settings.RISK_REWARD_RATIO
    kelly = compute_half_kelly(win_rate, rr)
    vol_scalar = compute_volatility_scalar(atr, atr_avg_30)

    if is_tanking:
        optimal_pct = 0.0
        status = "Tanking"
    elif volatile:
        optimal_pct = 0.0
        status = "Suppressed"
    else:
        optimal_pct = kelly * vol_scalar
        status = "Healthy Trend"

    optimal_usd = optimal_pct * available_capital

    # BUY signal
    golden_cross = ema_20_prev <= ema_50_prev and ema_20 > ema_50
    above_200 = price > ema_200
    rsi_buy_zone = 45 <= rsi <= 55

    if golden_cross and above_200 and rsi_buy_zone:
        stop_loss = price - (settings.ATR_STOP_MULTIPLIER * atr)
        risk = price - stop_loss
        reward = risk * rr
        target_price = price + reward
        actual_rr = reward / risk if risk > 0 else 0

        return SignalResult(
            direction="BUY",
            status=status,
            trigger_price=round(price, 6),
            stop_loss=round(stop_loss, 6),
            target_price=round(target_price, 6),
            reason="EMA Golden Cross + Price above 200 EMA + RSI stable zone",
            risk_reward=round(actual_rr, 2),
            atr_value=round(atr, 6),
            rsi_value=round(rsi, 2),
            suppressed=is_tanking or volatile,
            kelly_pct=round(optimal_pct * 100, 4),
            optimal_size_usd=round(optimal_usd, 2),
            volatility_scalar=round(vol_scalar, 6),
        )

    # SELL signal
    below_50 = price < ema_50
    rsi_overbought = rsi > 75

    if below_50 or rsi_overbought:
        stop_loss = price + (settings.ATR_STOP_MULTIPLIER * atr)
        risk = stop_loss - price
        reward = risk * rr
        target_price = price - reward
        actual_rr = reward / risk if risk > 0 else 0
        reasons = []
        if below_50:
            reasons.append("Price below EMA 50")
        if rsi_overbought:
            reasons.append(f"RSI overbought ({rsi:.1f})")
        if is_tanking and tanking_reason:
            reasons.append(f"TANKING: {tanking_reason}")

        return SignalResult(
            direction="SELL",
            status=status,
            trigger_price=round(price, 6),
            stop_loss=round(stop_loss, 6),
            target_price=round(target_price, 6),
            reason=" + ".join(reasons),
            risk_reward=round(actual_rr, 2),
            atr_value=round(atr, 6),
            rsi_value=round(rsi, 2),
            suppressed=False,
            kelly_pct=round(optimal_pct * 100, 4),
            optimal_size_usd=round(optimal_usd, 2),
            volatility_scalar=round(vol_scalar, 6),
        )

    return None
