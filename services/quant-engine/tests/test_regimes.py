from __future__ import annotations

import numpy as np
import pandas as pd

from app.backtesting import add_as_of_breadth, prepare_candles
from app.regimes import (
    classify_breadth,
    classify_prepared_row,
    classify_regime,
    regime_controls,
    timeframe_confluence,
)


def row(
    *,
    close: float,
    ema_20: float,
    ema_50: float,
    ema_200: float,
    atr: float = 2.0,
    atr_avg_30: float = 2.0,
) -> pd.Series:
    return pd.Series({
        "close": close,
        "ema_20": ema_20,
        "ema_50": ema_50,
        "ema_200": ema_200,
        "atr": atr,
        "atr_avg_30": atr_avg_30,
    })


def candles(
    closes: np.ndarray,
    *,
    frequency: str = "1D",
    start: str = "2024-01-01",
) -> pd.DataFrame:
    values = np.asarray(closes, dtype=float)
    timestamps = pd.date_range(start, periods=len(values), freq=frequency, tz="UTC")
    spread = np.maximum(1.0, values * 0.01)
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": values,
        "high": values + spread,
        "low": values - spread,
        "close": values,
        "volume": np.full(len(values), 1_000.0),
    })


def test_classifier_covers_trend_volatility_breadth_and_risk_states() -> None:
    bullish = classify_prepared_row(
        row(close=120, ema_20=115, ema_50=110, ema_200=100, atr=1, atr_avg_30=2),
        "stock",
        75,
    )
    bearish = classify_prepared_row(
        row(close=80, ema_20=85, ema_50=90, ema_200=100, atr=4, atr_avg_30=2),
        "crypto",
        25,
    )
    sideways = classify_prepared_row(
        row(close=101, ema_20=99, ema_50=100, ema_200=100),
        "stock",
        50,
    )

    assert (bullish.trend, bullish.volatility, bullish.breadth, bullish.risk) == (
        "bull", "low", "strong", "risk_on",
    )
    assert (bearish.trend, bearish.volatility, bearish.breadth, bearish.risk) == (
        "bear", "high", "weak", "risk_off",
    )
    assert (sideways.trend, sideways.volatility, sideways.breadth, sideways.risk) == (
        "sideways", "normal", "neutral", "neutral",
    )
    assert bullish.session_profile.startswith("US equity")
    assert bearish.session_profile == "24/7 UTC sessions"


def test_insufficient_history_returns_explicit_unknown() -> None:
    snapshot = classify_regime(candles(np.linspace(100, 120, 200)), "stock")

    assert snapshot.trend == "unknown"
    assert snapshot.risk == "unknown"
    assert snapshot.confidence == 0
    assert "Unknown" in snapshot.label


def test_duplicate_cleanup_cannot_bypass_minimum_history() -> None:
    frame = candles(np.linspace(100, 120, 201))
    frame.loc[200, "timestamp"] = frame.loc[199, "timestamp"]

    assert classify_regime(frame, "stock").trend == "unknown"


def test_cross_sectional_breadth_is_deterministic() -> None:
    rising = candles(np.linspace(50, 150, 220))
    falling = candles(np.linspace(150, 50, 220))

    strong = classify_breadth([rising, rising.copy(), falling])
    weak = classify_breadth([rising, falling, falling.copy()])

    assert strong == {"label": "strong", "pct_above_50": 66.7, "eligible_assets": 3}
    assert weak == {"label": "weak", "pct_above_50": 33.3, "eligible_assets": 3}
    assert classify_breadth([rising])["label"] == "unknown"


def test_timeframe_confluence_and_regime_controls_use_asset_specific_weights() -> None:
    bullish = candles(np.linspace(100, 140, 60), frequency="1h")
    bearish = candles(np.linspace(140, 100, 60), frequency="1h")
    frames = {"1d": bullish, "4h": bullish, "1h": bearish}

    stock = timeframe_confluence(frames, "BUY", "stock")
    crypto = timeframe_confluence(frames, "BUY", "crypto")
    snapshot = classify_prepared_row(
        row(close=120, ema_20=115, ema_50=110, ema_200=100), "stock", 70,
    )
    controls = regime_controls(snapshot, "BUY", stock)

    assert stock["score"] == 80.0
    assert crypto["score"] == 75.0
    assert stock["available"] is True
    assert controls["allowed"] is True
    assert controls["size_multiplier"] == 0.8


def test_adverse_regime_and_low_confluence_reject_setup() -> None:
    bearish = candles(np.linspace(140, 100, 60), frequency="1h")
    confluence = timeframe_confluence({"1d": bearish, "4h": bearish, "1h": bearish}, "BUY", "stock")
    snapshot = classify_prepared_row(
        row(close=80, ema_20=85, ema_50=90, ema_200=100, atr=4, atr_avg_30=2),
        "stock",
        20,
    )
    controls = regime_controls(snapshot, "BUY", confluence)

    assert controls["allowed"] is False
    assert controls["size_multiplier"] <= 0.125
    assert any("Regime fit" in reason for reason in controls["reasons"])
    assert any("Timeframe agreement" in reason for reason in controls["reasons"])


def test_as_of_breadth_does_not_use_future_peer_prices() -> None:
    target = prepare_candles(candles(np.linspace(100, 150, 240)))
    peer = candles(np.linspace(100, 150, 240))
    cutoff = 210
    baseline = add_as_of_breadth(target, [prepare_candles(peer)])
    mutated_peer = peer.copy()
    mutated_peer.loc[cutoff:, "close"] = 1.0
    mutated_peer.loc[cutoff:, "open"] = 1.0
    mutated_peer.loc[cutoff:, "high"] = 2.0
    mutated_peer.loc[cutoff:, "low"] = 0.5
    mutated = add_as_of_breadth(target, [prepare_candles(mutated_peer)])

    pd.testing.assert_series_equal(
        baseline.loc[: cutoff - 1, "breadth_pct_above_50"],
        mutated.loc[: cutoff - 1, "breadth_pct_above_50"],
    )


def test_regime_transition_uses_only_latest_available_candles() -> None:
    bullish = candles(np.linspace(100, 220, 280))
    transition = bullish.copy()
    transition.loc[220:, "close"] = np.linspace(210, 40, 60)
    transition.loc[220:, "open"] = transition.loc[220:, "close"]
    transition.loc[220:, "high"] = transition.loc[220:, "close"] + 2
    transition.loc[220:, "low"] = transition.loc[220:, "close"] - 2

    before = classify_regime(transition.iloc[:220], "stock", 75)
    after = classify_regime(transition, "stock", 25)

    assert before.trend == "bull"
    assert after.trend == "bear"
    assert after.risk == "risk_off"
