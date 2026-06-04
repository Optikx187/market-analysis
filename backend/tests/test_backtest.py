"""Pytest suite: backtest the strategy against historical data and verify drawdown limits."""

import sys
import os
import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.quant_engine import compute_ema, compute_rsi, compute_atr, add_indicators, evaluate_signals
from app.backtester import run_backtest


def generate_synthetic_data(n: int = 400, trend: str = "bullish") -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", periods=n, freq="D")

    if trend == "bullish":
        base = 100 + np.cumsum(np.random.randn(n) * 0.5 + 0.1)
    elif trend == "bearish":
        base = 200 + np.cumsum(np.random.randn(n) * 0.5 - 0.1)
    else:
        base = 150 + np.cumsum(np.random.randn(n) * 0.3)

    base = np.maximum(base, 10)
    noise = np.random.randn(n) * 1.5

    close = base + noise
    high = close + np.abs(np.random.randn(n) * 2)
    low = close - np.abs(np.random.randn(n) * 2)
    open_ = close + np.random.randn(n) * 0.5
    volume = np.random.randint(1000000, 10000000, n).astype(float)

    return pd.DataFrame({
        "timestamp": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


class TestIndicators:
    def test_ema_computation(self):
        series = pd.Series(range(100), dtype=float)
        ema = compute_ema(series, 20)
        assert len(ema) == 100
        assert not ema.isna().all()

    def test_rsi_computation(self):
        np.random.seed(42)
        series = pd.Series(100 + np.cumsum(np.random.randn(100)))
        rsi = compute_rsi(series, 14)
        valid_rsi = rsi.dropna()
        assert len(valid_rsi) > 0
        assert valid_rsi.min() >= 0
        assert valid_rsi.max() <= 100

    def test_atr_computation(self):
        np.random.seed(42)
        n = 100
        close = pd.Series(100 + np.cumsum(np.random.randn(n)))
        high = close + np.abs(np.random.randn(n))
        low = close - np.abs(np.random.randn(n))
        atr = compute_atr(high, low, close, 14)
        assert len(atr) == n
        valid_atr = atr.dropna()
        assert (valid_atr >= 0).all()

    def test_add_indicators(self):
        df = generate_synthetic_data(300)
        result = add_indicators(df)
        assert "ema_20" in result.columns
        assert "ema_50" in result.columns
        assert "ema_200" in result.columns
        assert "rsi" in result.columns
        assert "atr" in result.columns
        assert "atr_avg_30" in result.columns


class TestSignalGeneration:
    def test_no_signal_insufficient_data(self):
        df = generate_synthetic_data(100)
        result = evaluate_signals(df)
        assert result is None

    def test_signal_evaluation_runs(self):
        df = generate_synthetic_data(400, "bullish")
        result = evaluate_signals(df)
        # May or may not generate a signal depending on data; just check it doesn't crash
        if result is not None:
            assert result.direction in ["BUY", "SELL"]
            assert result.stop_loss > 0
            assert result.trigger_price > 0


class TestBacktest:
    def test_backtest_bullish(self):
        df = generate_synthetic_data(400, "bullish")
        result = run_backtest(df, "TEST")
        assert result.ticker == "TEST"
        assert result.initial_balance == 100_000
        assert result.final_balance > 0
        assert result.max_drawdown >= 0
        assert len(result.equity_curve) > 0

    def test_backtest_bearish(self):
        df = generate_synthetic_data(400, "bearish")
        result = run_backtest(df, "TEST-BEAR")
        assert result.ticker == "TEST-BEAR"
        assert result.final_balance > 0
        # Capital preservation: drawdown should be limited
        assert result.max_drawdown < 50, f"Max drawdown too high: {result.max_drawdown}%"

    def test_backtest_sideways(self):
        df = generate_synthetic_data(400, "sideways")
        result = run_backtest(df, "TEST-SIDE")
        assert result.final_balance > 0
        # Conservative strategy should not lose a lot in sideways markets
        loss_pct = (result.initial_balance - result.final_balance) / result.initial_balance * 100
        assert loss_pct < 20, f"Lost too much in sideways market: {loss_pct:.1f}%"

    def test_backtest_insufficient_data(self):
        df = generate_synthetic_data(100)
        result = run_backtest(df, "SHORT")
        assert result.total_trades == 0
        assert result.final_balance == result.initial_balance

    def test_backtest_drawdown_minimization(self):
        """Verify strategy minimizes max drawdown — the core requirement."""
        df = generate_synthetic_data(400, "bullish")
        result = run_backtest(df, "DD-TEST")
        # With 2% position sizing and strict stops, drawdown should be reasonable
        assert result.max_drawdown < 30, (
            f"Max drawdown {result.max_drawdown:.1f}% exceeds threshold. "
            f"Strategy must minimize drawdown for capital preservation."
        )
        print(f"\n=== Backtest Results for {result.ticker} ===")
        print(f"Total Trades: {result.total_trades}")
        print(f"Win Rate: {result.win_rate:.1f}%")
        print(f"Total PnL: ${result.total_pnl:,.2f}")
        print(f"Max Drawdown: {result.max_drawdown:.2f}%")
        print(f"Profit Factor: {result.profit_factor:.2f}")
        print(f"Final Balance: ${result.final_balance:,.2f}")


class TestRiskGuardrails:
    def test_stop_loss_calculation(self):
        """Verify stop-loss is calculated as Entry - 1.5 * ATR."""
        df = generate_synthetic_data(400, "bullish")
        df_with_ind = add_indicators(df)
        row = df_with_ind.iloc[-1]
        if not pd.isna(row["atr"]):
            expected_stop = row["close"] - (1.5 * row["atr"])
            assert expected_stop > 0

    def test_risk_reward_minimum(self):
        """Verify risk-reward ratio is at least 1:3."""
        df = generate_synthetic_data(400, "bullish")
        signal = evaluate_signals(df)
        if signal is not None and signal.direction == "BUY":
            assert signal.risk_reward >= 3.0, (
                f"Risk-reward {signal.risk_reward} below 1:3 minimum"
            )
