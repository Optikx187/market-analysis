from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app import backtesting
from app.backtest_store import BacktestStore
from app.backtesting import (
    ExecutionCosts,
    StrategyParameters,
    TradeSignal,
    ValidationThresholds,
    WindowConfiguration,
    build_walk_forward_windows,
    calculate_buy_and_hold,
    prepare_candles,
    run_walk_forward_backtest,
    simulate_segment,
)


def make_candles(length: int = 380) -> pd.DataFrame:
    index = np.arange(length)
    close = 100 + index * 0.04 + 7 * np.sin(index / 11)
    return pd.DataFrame({
        "timestamp": pd.date_range("2023-01-01", periods=length, freq="D"),
        "open": close * 0.999,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": np.full(length, 1_000_000.0),
    })


def configuration() -> WindowConfiguration:
    return WindowConfiguration(
        warmup_bars=201,
        train_bars=60,
        validation_bars=20,
        test_bars=20,
        step_bars=20,
    )


def test_walk_forward_windows_are_chronological_and_test_windows_do_not_overlap() -> None:
    windows = build_walk_forward_windows(400, configuration())

    assert len(windows) == 5
    for window in windows:
        assert window.train_end == window.validation_start
        assert window.validation_end == window.test_start
        assert window.train_end <= window.validation_start
        assert window.validation_end <= window.test_start
    for previous, current in zip(windows, windows[1:]):
        assert previous.test_end <= current.test_start


def test_future_prices_do_not_change_an_earlier_window() -> None:
    candles = make_candles(360)
    parameters = [StrategyParameters(2.0, 1.0), StrategyParameters(3.0, 1.5)]
    baseline = run_walk_forward_backtest(
        "TEST",
        candles,
        "ema-rsi-atr-v1",
        parameters,
        ExecutionCosts(),
        configuration(),
        ValidationThresholds(),
        10_000,
    )
    first_test_end = baseline["windows"][0]["boundaries"]["test"]["end_index"]
    changed = candles.copy()
    changed.loc[int(first_test_end) + 1:, ["open", "high", "low", "close"]] *= 4
    rerun = run_walk_forward_backtest(
        "TEST",
        changed,
        "ema-rsi-atr-v1",
        parameters,
        ExecutionCosts(),
        configuration(),
        ValidationThresholds(),
        10_000,
    )

    assert baseline["windows"][0] == rerun["windows"][0]


def test_commission_spread_and_slippage_reduce_after_cost_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_candles(make_candles(240))

    def one_long_signal(
        frame: pd.DataFrame, index: int, parameters: StrategyParameters,
    ) -> TradeSignal | None:
        if index != 210:
            return None
        return TradeSignal(
            direction="BUY",
            trigger_price=float(frame.iloc[index]["close"]),
            stop_loss=1,
            target_price=1_000_000,
            reason="deterministic fixture",
            market_regime="bull",
            volatility_regime="normal",
        )

    monkeypatch.setattr(backtesting, "_signal_for_row", one_long_signal)
    no_costs = simulate_segment(
        prepared, 210, 230, StrategyParameters(), ExecutionCosts(0, 0, 0, 1), 10_000
    )
    with_costs = simulate_segment(
        prepared, 210, 230, StrategyParameters(), ExecutionCosts(10, 20, 15, 1), 10_000
    )

    assert no_costs.metrics.gross_return_pct == with_costs.metrics.gross_return_pct
    assert with_costs.metrics.after_cost_return_pct < no_costs.metrics.after_cost_return_pct
    assert with_costs.metrics.total_cost_pct > 0
    assert with_costs.trades[0].signal_date != with_costs.trades[0].entry_date


def test_same_bar_fills_are_rejected_to_prevent_look_ahead() -> None:
    with pytest.raises(ValueError, match="fill_delay_bars"):
        run_walk_forward_backtest(
            "TEST",
            make_candles(360),
            "ema-rsi-atr-v1",
            [StrategyParameters()],
            ExecutionCosts(fill_delay_bars=0),
            configuration(),
            ValidationThresholds(),
            10_000,
        )


def test_buy_and_hold_benchmark_calculation() -> None:
    candles = pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=3, freq="D"),
        "open": [100.0, 104.0, 108.0],
        "high": [102.0, 106.0, 111.0],
        "low": [99.0, 103.0, 107.0],
        "close": [101.0, 105.0, 110.0],
        "volume": [1_000.0, 1_000.0, 1_000.0],
    })
    benchmark = calculate_buy_and_hold(
        "TEST", prepare_candles(candles), 0, 3, ExecutionCosts(0, 0, 0, 1), 10_000
    )

    assert benchmark.gross_return_pct == pytest.approx(10.0)
    assert benchmark.after_cost_return_pct == pytest.approx(10.0)
    assert benchmark.final_equity == pytest.approx(11_000.0)


def test_result_contains_sample_splits_sensitivity_regimes_and_eligibility() -> None:
    result = run_walk_forward_backtest(
        "TEST",
        make_candles(360),
        "strategy-version-7",
        [StrategyParameters(2.0, 1.0), StrategyParameters(3.0, 1.5)],
        ExecutionCosts(),
        configuration(),
        ValidationThresholds(minimum_trades=999),
        10_000,
        benchmark_frames={"SPY": make_candles(360)},
    )

    assert result["strategy"]["version"] == "strategy-version-7"
    assert set(result["aggregate"]) == {"in_sample", "validation", "out_of_sample"}
    assert len(result["parameter_sensitivity"]) == 2
    assert "TEST" in result["benchmarks"]
    assert result["benchmarks"]["SPY"]["windows"] == result["window_count"]
    assert result["alert_eligibility"]["eligible"] is False
    assert "999" in result["alert_eligibility"]["reasons"][0]


def test_backtest_store_persists_strategy_request_and_result(tmp_path: Path) -> None:
    store = BacktestStore(str(tmp_path / "backtests.db"))
    result: dict[str, object] = {
        "ticker": "BTC",
        "alert_eligibility": {"eligible": True, "reasons": []},
    }
    request: dict[str, object] = {
        "ticker": "BTC",
        "strategy_version": "v2",
        "parameter_grid": [{"risk_reward_ratio": 3.0}],
    }

    run_id = store.save("BTC", "v2", request, result)
    stored = store.get(run_id)
    summaries = store.list("BTC")

    assert stored is not None
    assert stored["run_id"] == run_id
    assert stored["created_at"]
    assert summaries == [{
        "run_id": run_id,
        "ticker": "BTC",
        "strategy_version": "v2",
        "created_at": stored["created_at"],
        "alert_eligible": True,
    }]
