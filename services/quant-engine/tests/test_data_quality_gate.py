import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app import main


def quality(ticker: str, eligible: bool, issues: list[str]) -> main.DataQualityResponse:
    return main.DataQualityResponse(
        ticker=ticker,
        asset_type="stock",
        interval="1d",
        status="healthy" if eligible else "stale",
        is_eligible=eligible,
        candle_count=252,
        latest_timestamp="2026-07-15T00:00:00+00:00",
        age_hours=12,
        stale=not eligible,
        duplicate_timestamps=0,
        missing_periods=0,
        invalid_timestamps=0,
        invalid_ohlc=0,
        anomaly_count=0,
        issues=issues,
    )


def test_analyze_rejects_stale_data_before_signal_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_quality = AsyncMock(return_value=quality("AAPL", False, ["Latest candle is stale"]))
    fetch_candles = AsyncMock()
    monkeypatch.setattr(main, "_fetch_data_quality", fetch_quality)
    monkeypatch.setattr(main, "fetch_candles_from_service_a", fetch_candles)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.analyze(main.AnalyzeRequest(ticker="AAPL")))

    assert exc.value.status_code == 422
    assert "Latest candle is stale" in exc.value.detail
    fetch_candles.assert_not_awaited()


def test_scan_reports_quality_rejections_without_fetching_candles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main,
        "_fetch_assets",
        AsyncMock(return_value=[{"ticker": "AAPL", "asset_type": "stock", "is_active": True}]),
    )
    monkeypatch.setattr(
        main,
        "_fetch_all_data_quality",
        AsyncMock(return_value={"AAPL": quality("AAPL", False, ["Latest candle is stale"])}),
    )
    fetch_candles = AsyncMock()
    monkeypatch.setattr(main, "fetch_candles_from_service_a", fetch_candles)

    result = asyncio.run(main._run_scan())

    assert result["scanned"] == 0
    assert result["quality_rejected"] == 1
    assert result["quality_rejections"] == [
        {"ticker": "AAPL", "reason": "Latest candle is stale"}
    ]
    assert result["notifications_sent"] == 0
    fetch_candles.assert_not_awaited()


def test_scan_fails_closed_when_quality_service_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main,
        "_fetch_assets",
        AsyncMock(return_value=[{"ticker": "BTC", "asset_type": "crypto", "is_active": True}]),
    )
    monkeypatch.setattr(
        main,
        "_fetch_all_data_quality",
        AsyncMock(side_effect=RuntimeError("unavailable")),
    )
    fetch_candles = AsyncMock()
    monkeypatch.setattr(main, "fetch_candles_from_service_a", fetch_candles)

    result = asyncio.run(main._run_scan())

    assert result["quality_rejected"] == 1
    assert result["quality_rejections"] == [
        {"ticker": "BTC", "reason": "Data quality service unavailable"}
    ]
    fetch_candles.assert_not_awaited()
