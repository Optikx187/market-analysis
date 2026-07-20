import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd

from app import main
from app.backtest_store import BacktestStore


def make_candles(length: int = 340) -> pd.DataFrame:
    index = np.arange(length)
    close = 100 + index * 0.03 + 6 * np.sin(index / 10)
    return pd.DataFrame({
        "timestamp": pd.date_range("2023-01-01", periods=length, freq="D"),
        "open": close * 0.999,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": np.full(length, 1_000_000.0),
    })


def test_backtest_api_returns_and_persists_walk_forward_result(
    monkeypatch, tmp_path: Path,
) -> None:
    store = BacktestStore(str(tmp_path / "runs.db"))
    monkeypatch.setattr(main, "_backtest_store", store)
    monkeypatch.setattr(main, "_require_eligible_data", AsyncMock())
    monkeypatch.setattr(main, "fetch_candles_from_service_a", AsyncMock(return_value=make_candles()))

    result = asyncio.run(main.backtest(main.BacktestRequest(ticker="btc")))
    history = asyncio.run(main.list_backtest_runs(ticker="btc"))
    loaded = asyncio.run(main.get_backtest_run(result["run_id"]))

    assert result["ticker"] == "BTC"
    assert result["window_count"] == 2
    assert set(result["aggregate"]) == {"in_sample", "validation", "out_of_sample"}
    assert result["configuration"]["costs"]["fill_delay_bars"] == 1
    assert history[0]["run_id"] == result["run_id"]
    assert loaded == result
