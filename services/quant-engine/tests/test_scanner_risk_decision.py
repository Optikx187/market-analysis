import asyncio
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app import main
from app.signals import SignalResult


def _quality(ticker: str) -> main.DataQualityResponse:
    return main.DataQualityResponse(
        ticker=ticker,
        asset_type="stock",
        interval="1d",
        status="healthy",
        is_eligible=True,
        candle_count=252,
        latest_timestamp="2026-07-15T00:00:00+00:00",
        age_hours=12,
        stale=False,
        duplicate_timestamps=0,
        missing_periods=0,
        invalid_timestamps=0,
        invalid_ohlc=0,
        anomaly_count=0,
        issues=[],
    )


def _signal() -> SignalResult:
    return SignalResult(
        direction="BUY",
        status="Healthy Trend",
        trigger_price=100,
        stop_loss=95,
        target_price=115,
        reason="Deterministic signal",
        risk_reward=3,
        atr_value=2,
        rsi_value=55,
        suppressed=False,
        kelly_pct=20,
        optimal_size_usd=3_000,
        volatility_scalar=1,
    )


def _prepare_scan(monkeypatch: pytest.MonkeyPatch, ticker: str) -> None:
    main._recent_signals.clear()
    monkeypatch.setattr(
        main,
        "_fetch_assets",
        AsyncMock(return_value=[{"ticker": ticker, "asset_type": "stock", "is_active": True}]),
    )
    monkeypatch.setattr(
        main,
        "_fetch_all_data_quality",
        AsyncMock(return_value={ticker: _quality(ticker)}),
    )
    monkeypatch.setattr(
        main,
        "fetch_candles_from_service_a",
        AsyncMock(return_value=pd.DataFrame({"close": range(201)})),
    )
    monkeypatch.setattr(main, "evaluate_signals", lambda frame, capital: _signal())


def test_scanner_notifies_with_risk_recommended_size(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_scan(monkeypatch, "AAPL")
    monkeypatch.setattr(
        main,
        "_process_signal_via_portfolio",
        AsyncMock(return_value={
            "approved": True,
            "reason": "Size reduced by fractional-Kelly cap",
            "optimal_size_usd": 1_000,
            "risk_decision": {"action": "reduced"},
        }),
    )
    send_notification = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "_send_notification", send_notification)

    result = asyncio.run(main._run_scan())

    assert result["notifications_sent"] == 1
    assert result["signals"] == [{
        "ticker": "AAPL",
        "direction": "BUY",
        "status": "Healthy Trend",
        "approved": True,
        "suppressed": False,
        "action": "reduced",
        "reason": "Size reduced by fractional-Kelly cap",
        "recommended_size_usd": 1_000,
    }]
    assert send_notification.await_args.args[0]["optimal_size_usd"] == 1_000


def test_scanner_exposes_rejection_reason_without_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_scan(monkeypatch, "MSFT")
    monkeypatch.setattr(
        main,
        "_process_signal_via_portfolio",
        AsyncMock(return_value={
            "approved": False,
            "reason": "Ticker exposure for MSFT 30.00% exceeds 20.00% limit",
            "optimal_size_usd": 0,
            "risk_decision": {"action": "rejected"},
        }),
    )
    send_notification = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "_send_notification", send_notification)

    result = asyncio.run(main._run_scan())

    assert result["notifications_sent"] == 0
    assert result["signals"][0]["approved"] is False
    assert result["signals"][0]["action"] == "rejected"
    assert result["signals"][0]["recommended_size_usd"] == 0
    assert result["signals"][0]["reason"] == "Ticker exposure for MSFT 30.00% exceeds 20.00% limit"
    send_notification.assert_not_awaited()
