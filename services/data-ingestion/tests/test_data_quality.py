from datetime import datetime, timezone

import pandas as pd
import pytest

from app.data_quality import (
    _missing_period_count,
    _stock_intraday_is_stale,
    assess_data_quality,
)
from app.ingestion import _resample_four_hour, validate_candles_for_storage
from app.models import AssetType


def candle_frame(timestamps: pd.DatetimeIndex) -> pd.DataFrame:
    prices = pd.Series(range(len(timestamps)), dtype=float) * 0.1 + 100
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": prices + 1,
        "low": prices - 1,
        "close": prices + 0.25,
        "volume": 1_000.0,
    })


def test_fresh_crypto_is_eligible() -> None:
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    candles = candle_frame(pd.date_range(end="2026-07-15", periods=201, freq="D", tz="UTC"))

    report = assess_data_quality("BTC", AssetType.CRYPTO, candles, now=now)

    assert report.status == "healthy"
    assert report.is_eligible is True
    assert report.stale is False
    assert report.missing_periods == 0


def test_stock_is_fresh_during_weekend_and_before_monday_close() -> None:
    candles = candle_frame(pd.bdate_range(end="2026-07-10", periods=201, tz="UTC"))

    weekend = assess_data_quality(
        "SPY", AssetType.STOCK, candles,
        now=datetime(2026, 7, 12, 18, tzinfo=timezone.utc),
    )
    before_close = assess_data_quality(
        "SPY", AssetType.STOCK, candles,
        now=datetime(2026, 7, 13, 14, tzinfo=timezone.utc),
    )

    assert weekend.stale is False
    assert before_close.stale is False
    assert weekend.is_eligible is True
    assert before_close.is_eligible is True


def test_stock_holiday_does_not_require_a_nonexistent_session() -> None:
    timestamps = pd.bdate_range(end="2026-07-06", periods=202, tz="UTC")
    timestamps = timestamps[timestamps != pd.Timestamp("2026-07-03", tz="UTC")]
    candles = candle_frame(timestamps)

    holiday = assess_data_quality(
        "SPY", AssetType.STOCK, candles.iloc[:-1],
        now=datetime(2026, 7, 3, 21, tzinfo=timezone.utc),
    )
    monday_close = assess_data_quality(
        "SPY", AssetType.STOCK, candles,
        now=datetime(2026, 7, 6, 21, tzinfo=timezone.utc),
    )

    assert holiday.stale is False
    assert holiday.missing_periods == 0
    assert monday_close.stale is False
    assert monday_close.missing_periods == 0


def test_stale_crypto_is_blocked() -> None:
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    candles = candle_frame(pd.date_range(end="2026-07-12", periods=201, freq="D", tz="UTC"))

    report = assess_data_quality("BTC", AssetType.CRYPTO, candles, now=now)

    assert report.status == "stale"
    assert report.is_eligible is False
    assert "Latest candle is stale" in report.issues


def test_duplicate_and_invalid_ohlcv_are_blocked() -> None:
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    candles = candle_frame(pd.date_range(end="2026-07-15", periods=201, freq="D", tz="UTC"))
    candles.loc[20, "timestamp"] = candles.loc[19, "timestamp"]
    candles.loc[30, "high"] = candles.loc[30, "low"] - 1
    candles.loc[31, "volume"] = -1

    report = assess_data_quality("BTC", AssetType.CRYPTO, candles, now=now)

    assert report.status == "invalid"
    assert report.is_eligible is False
    assert report.duplicate_timestamps == 1
    assert report.invalid_ohlc == 2


def test_missing_period_is_warning_but_not_hard_block() -> None:
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    timestamps = pd.date_range(end="2026-07-15", periods=202, freq="D", tz="UTC").delete(100)
    candles = candle_frame(timestamps)

    report = assess_data_quality("BTC", AssetType.CRYPTO, candles, now=now)

    assert report.status == "warning"
    assert report.is_eligible is True
    assert report.missing_periods == 1


def test_insufficient_history_is_blocked() -> None:
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    candles = candle_frame(pd.date_range(end="2026-07-15", periods=50, freq="D", tz="UTC"))

    report = assess_data_quality("ETH", AssetType.CRYPTO, candles, now=now)

    assert report.status == "insufficient"
    assert report.is_eligible is False
    assert "Insufficient history: 50/201 candles" in report.issues


def test_storage_validation_rejects_malformed_provider_data() -> None:
    candles = candle_frame(pd.date_range(end="2026-07-15", periods=3, freq="D", tz="UTC"))
    candles.loc[1, "open"] = float("nan")

    with pytest.raises(ValueError, match="1 malformed row"):
        validate_candles_for_storage(candles)


def test_storage_validation_normalizes_provider_values() -> None:
    candles = candle_frame(pd.date_range(end="2026-07-15", periods=3, freq="D", tz="UTC"))
    candles["close"] = candles["close"].astype(str)

    validated = validate_candles_for_storage(candles)

    assert validated["timestamp"].dt.tz is not None
    assert validated["close"].dtype.kind == "f"


def test_crypto_intraday_freshness_uses_interval_thresholds() -> None:
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    fresh = candle_frame(pd.date_range(end="2026-07-15 10:00", periods=201, freq="1h", tz="UTC"))
    stale = candle_frame(pd.date_range(end="2026-07-15 08:00", periods=201, freq="1h", tz="UTC"))

    fresh_report = assess_data_quality("BTC", AssetType.CRYPTO, fresh, interval="1h", now=now)
    stale_report = assess_data_quality("BTC", AssetType.CRYPTO, stale, interval="1h", now=now)

    assert fresh_report.stale is False
    assert fresh_report.is_eligible is True
    assert stale_report.stale is True
    assert stale_report.is_eligible is False


def test_stock_intraday_freshness_respects_market_hours_and_weekends() -> None:
    friday_close = datetime(2026, 7, 10, 19, 30, tzinfo=timezone.utc)

    assert _stock_intraday_is_stale(
        friday_close, datetime(2026, 7, 12, 18, tzinfo=timezone.utc), "1h",
    ) is False
    assert _stock_intraday_is_stale(
        friday_close, datetime(2026, 7, 13, 14, tzinfo=timezone.utc), "1h",
    ) is False
    assert _stock_intraday_is_stale(
        friday_close, datetime(2026, 7, 13, 16, tzinfo=timezone.utc), "1h",
    ) is True
    assert _stock_intraday_is_stale(
        friday_close, datetime(2026, 7, 13, 21, tzinfo=timezone.utc), "1h",
    ) is True


def test_missing_stock_intraday_session_counts_expected_bars() -> None:
    timestamps = pd.DatetimeIndex([
        *pd.date_range("2026-07-13 13:30", periods=7, freq="1h", tz="UTC"),
        *pd.date_range("2026-07-15 13:30", periods=7, freq="1h", tz="UTC"),
    ])

    assert _missing_period_count(pd.Series(timestamps), AssetType.STOCK, "1h") == 7


def test_stock_four_hour_resampling_never_crosses_equity_sessions() -> None:
    first_session = pd.date_range("2026-07-13 13:30", periods=7, freq="1h", tz="UTC")
    second_session = pd.date_range("2026-07-14 13:30", periods=7, freq="1h", tz="UTC")
    hourly = candle_frame(first_session.append(second_session))

    resampled = _resample_four_hour(hourly, AssetType.STOCK)

    assert len(resampled) == 4
    assert resampled["timestamp"].dt.date.nunique() == 2
    assert resampled.groupby(resampled["timestamp"].dt.date).size().tolist() == [2, 2]


def test_crypto_four_hour_resampling_is_continuous_utc() -> None:
    hourly = candle_frame(pd.date_range("2026-07-13", periods=24, freq="1h", tz="UTC"))

    resampled = _resample_four_hour(hourly, AssetType.CRYPTO)

    assert len(resampled) == 6
    assert resampled["timestamp"].dt.hour.tolist() == [0, 4, 8, 12, 16, 20]
