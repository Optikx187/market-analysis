import asyncio
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app import ingestion
from app.models import AssetType


def candle_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=2, freq="D"),
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
        "volume": [1_000.0, 1_100.0],
    })


def test_crypto_refresh_requests_one_thousand_daily_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_binance = AsyncMock(return_value=candle_frame())
    monkeypatch.setattr(ingestion, "fetch_historical_binance", fetch_binance)

    result = asyncio.run(ingestion.fetch_historical("BTC", AssetType.CRYPTO))

    assert len(result) == 2
    fetch_binance.assert_awaited_once_with("BTCUSDT", "1d", 1000)


def test_crypto_fallback_requests_three_years_from_yfinance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ingestion,
        "fetch_historical_binance",
        AsyncMock(side_effect=RuntimeError("provider unavailable")),
    )
    fetch_yfinance = AsyncMock(return_value=candle_frame())
    monkeypatch.setattr(ingestion, "fetch_historical_yfinance", fetch_yfinance)

    asyncio.run(ingestion.fetch_historical("BTC", AssetType.CRYPTO))

    fetch_yfinance.assert_awaited_once_with("BTC-USD", "3y", "1d")


def test_stock_fallback_requests_three_years_from_yfinance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingestion, "fetch_historical_alpaca", AsyncMock(return_value=pd.DataFrame()))
    fetch_yfinance = AsyncMock(return_value=candle_frame())
    monkeypatch.setattr(ingestion, "fetch_historical_yfinance", fetch_yfinance)

    asyncio.run(ingestion.fetch_historical("AAPL", AssetType.STOCK))

    fetch_yfinance.assert_awaited_once_with("AAPL", "3y", "1d")
