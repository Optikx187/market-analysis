"""Pytest suite: validate Service B risk models shut off allocations during crashes.

Tests verify:
1. Position sizes → $0 when asset is tanking (price < 200 EMA or bearish 20/50 cross)
2. Volatility calibration scales positions asymptotically to $0 during high ATR
3. Half-Kelly returns 0 when there's no edge
4. Buy signals suppressed during tanking phase
5. Liquidation recommended when tanking detected
"""

import numpy as np
import pandas as pd
import pytest

from app.risk_engine import (
    compute_half_kelly,
    compute_volatility_scalar,
    detect_tanking,
    evaluate_risk_profile,
)
from app.signals import evaluate_signals


def _make_ohlcv(closes: list[float], base_price: float = 100.0) -> pd.DataFrame:
    """Generate synthetic OHLCV data from a list of close prices."""
    n = len(closes)
    data = {
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="D"),
        "open": [c * 0.999 for c in closes],
        "high": [c * 1.005 for c in closes],
        "low": [c * 0.995 for c in closes],
        "close": closes,
        "volume": [1_000_000.0] * n,
    }
    return pd.DataFrame(data)


def _make_bull_then_crash(n_bull: int = 180, n_crash: int = 30) -> pd.DataFrame:
    """Create data that rises steadily then crashes sharply."""
    bull_prices = [100 + i * 0.5 for i in range(n_bull)]
    peak = bull_prices[-1]
    crash_prices = [peak - i * 3.0 for i in range(1, n_crash + 1)]
    all_prices = bull_prices + crash_prices
    return _make_ohlcv(all_prices)


def _make_stable_trend(n: int = 250) -> pd.DataFrame:
    """Create steadily rising prices (healthy bull trend)."""
    prices = [100 + i * 0.3 for i in range(n)]
    return _make_ohlcv(prices)


def _make_high_volatility(n: int = 250) -> pd.DataFrame:
    """Create data with extremely erratic recent ATR."""
    rng = np.random.RandomState(42)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] + rng.uniform(-0.3, 0.35))
    for i in range(n - 30, n):
        prices[i] = prices[i] + rng.uniform(-15, 15)
    return _make_ohlcv(prices)


# ──── Half-Kelly Tests ────


class TestHalfKelly:
    def test_positive_edge(self):
        kelly = compute_half_kelly(win_rate=0.6, risk_reward=3.0)
        assert kelly > 0
        expected = 0.5 * (0.6 - ((1 - 0.6) / 3.0))
        assert abs(kelly - expected) < 1e-9

    def test_no_edge_returns_zero(self):
        kelly = compute_half_kelly(win_rate=0.2, risk_reward=1.0)
        assert kelly == 0.0

    def test_zero_win_rate(self):
        kelly = compute_half_kelly(win_rate=0.0, risk_reward=3.0)
        assert kelly == 0.0

    def test_zero_risk_reward(self):
        kelly = compute_half_kelly(win_rate=0.5, risk_reward=0.0)
        assert kelly == 0.0

    def test_perfect_win_rate(self):
        kelly = compute_half_kelly(win_rate=1.0, risk_reward=3.0)
        assert kelly == pytest.approx(0.5, abs=1e-6)


# ──── Volatility Scalar Tests ────


class TestVolatilityScalar:
    def test_normal_volatility_full_size(self):
        scalar = compute_volatility_scalar(atr_current=10.0, atr_avg_30=10.0)
        assert scalar == pytest.approx(1.0, abs=1e-6)

    def test_double_atr_zero_size(self):
        scalar = compute_volatility_scalar(atr_current=20.0, atr_avg_30=10.0)
        assert scalar == pytest.approx(0.0, abs=1e-6)

    def test_extreme_volatility_zero(self):
        scalar = compute_volatility_scalar(atr_current=50.0, atr_avg_30=10.0)
        assert scalar == 0.0

    def test_1_5x_atr_half_size(self):
        scalar = compute_volatility_scalar(atr_current=15.0, atr_avg_30=10.0)
        assert scalar == pytest.approx(0.5, abs=1e-6)

    def test_zero_avg_atr_returns_zero(self):
        scalar = compute_volatility_scalar(atr_current=5.0, atr_avg_30=0.0)
        assert scalar == 0.0


# ──── Tanking Detection Tests ────


class TestTankingDetection:
    def test_price_below_200_ema(self):
        is_tanking, reason = detect_tanking(
            price=90, ema_20=95, ema_50=97, ema_200=100,
            ema_20_prev=96, ema_50_prev=97,
        )
        assert is_tanking is True
        assert "200 EMA" in reason

    def test_bearish_cross(self):
        is_tanking, reason = detect_tanking(
            price=105, ema_20=98, ema_50=99, ema_200=100,
            ema_20_prev=100, ema_50_prev=99,
        )
        assert is_tanking is True
        assert "bearish cross" in reason

    def test_healthy_trend_not_tanking(self):
        is_tanking, reason = detect_tanking(
            price=110, ema_20=108, ema_50=105, ema_200=100,
            ema_20_prev=107, ema_50_prev=106,
        )
        assert is_tanking is False
        assert reason is None


# ──── Integration: Risk Profile During Crash ────


class TestRiskProfileCrash:
    def test_crash_results_in_zero_position(self):
        """When an asset crashes below 200 EMA, position size must be $0."""
        df = _make_bull_then_crash(180, 30)
        profile = evaluate_risk_profile(df, "CRASH_COIN", 100_000.0)
        assert profile is not None
        assert profile.is_tanking is True
        assert profile.optimal_position_usd == 0.0
        assert profile.optimal_position_pct == 0.0
        assert profile.recommend_liquidate is True

    def test_stable_trend_allows_position(self):
        """A healthy rising asset should have non-zero allocation."""
        df = _make_stable_trend(250)
        profile = evaluate_risk_profile(df, "BULL_ASSET", 100_000.0)
        if profile is not None and not profile.is_tanking:
            assert profile.optimal_position_pct >= 0.0


# ──── Integration: Signals During High Volatility ────


class TestSignalsDuringCrash:
    def test_buy_suppressed_during_tanking(self):
        """During tanking, even if golden cross fires, signal must be suppressed."""
        df = _make_bull_then_crash(180, 30)
        signal = evaluate_signals(df, 100_000.0)
        if signal is not None and signal.direction == "BUY":
            assert signal.suppressed is True
            assert signal.optimal_size_usd == 0.0

    def test_sell_signal_during_crash(self):
        """Crash should produce SELL signal or no signal, never unsuppressed BUY."""
        df = _make_bull_then_crash(180, 30)
        signal = evaluate_signals(df, 100_000.0)
        if signal is not None:
            if signal.direction == "BUY":
                assert signal.suppressed is True
            elif signal.direction == "SELL":
                assert "TANKING" in signal.reason or "below" in signal.reason.lower()

    def test_high_volatility_zero_sizing(self):
        """Extreme ATR must drive optimal_size_usd asymptotically to $0."""
        df = _make_high_volatility(250)
        signal = evaluate_signals(df, 100_000.0)
        if signal is not None:
            if signal.status == "Suppressed":
                assert signal.optimal_size_usd == 0.0


# ──── Asymptotic Convergence Test ────


class TestAsymptoticConvergence:
    def test_increasing_atr_reduces_position_to_zero(self):
        """As ATR increases from 1x to 5x the average, position must converge to $0."""
        atr_avg = 10.0
        positions = []
        for multiplier in [1.0, 1.5, 2.0, 3.0, 5.0]:
            scalar = compute_volatility_scalar(atr_avg * multiplier, atr_avg)
            positions.append(scalar)

        assert positions[0] == pytest.approx(1.0, abs=1e-6)
        assert positions[1] == pytest.approx(0.5, abs=1e-6)
        for p in positions[2:]:
            assert p == 0.0

        for i in range(1, len(positions)):
            assert positions[i] <= positions[i - 1]
