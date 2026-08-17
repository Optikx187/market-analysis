"""Market-data quality checks used to gate analysis and scanning."""

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
)
from pandas.tseries.offsets import CustomBusinessDay

from app.models import AssetType

MIN_ANALYSIS_CANDLES = 201
CRYPTO_STALE_AFTER = {
    "1d": timedelta(hours=48),
    "4h": timedelta(hours=12),
    "1h": timedelta(hours=3),
}
INTERVAL_DURATION = {
    "1d": timedelta(days=1),
    "4h": timedelta(hours=4),
    "1h": timedelta(hours=1),
}
STOCK_MARKET_OPEN = time(hour=9, minute=30)
STOCK_MARKET_CLOSE = time(hour=16)
STOCK_MARKET_CLOSE_BUFFER = time(hour=16, minute=30)


class UsEquityHolidayCalendar(AbstractHolidayCalendar):
    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=nearest_workday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday(
            "Juneteenth National Independence Day",
            month=6,
            day=19,
            start_date="2022-06-19",
            observance=nearest_workday,
        ),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas Day", month=12, day=25, observance=nearest_workday),
    ]


US_EQUITY_BUSINESS_DAY = CustomBusinessDay(calendar=UsEquityHolidayCalendar())


@dataclass(frozen=True)
class DataQualityReport:
    ticker: str
    asset_type: str
    interval: str
    status: str
    is_eligible: bool
    candle_count: int
    latest_timestamp: str | None
    age_hours: float | None
    stale: bool
    duplicate_timestamps: int
    missing_periods: int
    invalid_timestamps: int
    invalid_ohlc: int
    anomaly_count: int
    issues: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _is_stock_session(value: date) -> bool:
    return US_EQUITY_BUSINESS_DAY.is_on_offset(pd.Timestamp(value))


def _previous_stock_session(value: date) -> date:
    candidate = value - timedelta(days=1)
    while not _is_stock_session(candidate):
        candidate -= timedelta(days=1)
    return candidate


def _expected_stock_session(now: datetime) -> date:
    eastern_now = now.astimezone(ZoneInfo("America/New_York"))
    current_date = eastern_now.date()
    if _is_stock_session(current_date) and eastern_now.time() >= STOCK_MARKET_CLOSE_BUFFER:
        return current_date
    return _previous_stock_session(current_date)


def _missing_period_count(
    timestamps: pd.Series,
    asset_type: AssetType,
    interval: str,
) -> int:
    ordered = pd.DatetimeIndex(timestamps).unique().sort_values()
    if len(ordered) < 2:
        return 0
    if interval == "1d":
        dates = ordered.normalize()
        if asset_type == AssetType.CRYPTO:
            gaps = pd.Series(dates).diff().dt.days.dropna() - 1
            return int(gaps.clip(lower=0).sum())
        expected = pd.date_range(
            start=dates[0], end=dates[-1], freq=US_EQUITY_BUSINESS_DAY, tz="UTC"
        )
        return max(0, len(expected.difference(dates)))

    duration = INTERVAL_DURATION[interval]
    if asset_type == AssetType.CRYPTO:
        gaps = pd.Series(ordered).diff().dropna() / duration - 1
        return int(gaps.clip(lower=0).apply(int).sum())

    local = ordered.tz_convert("America/New_York")
    dates = pd.DatetimeIndex(local.normalize()).unique().sort_values()
    expected_dates = pd.date_range(
        start=dates[0], end=dates[-1], freq=US_EQUITY_BUSINESS_DAY,
        tz="America/New_York",
    )
    bars_per_session = 7 if interval == "1h" else 2
    missing_sessions = len(expected_dates.difference(dates)) * bars_per_session
    frame = pd.DataFrame({"timestamp": local, "session": local.date})
    intraday_missing = 0
    for _, group in frame.groupby("session"):
        gaps = group["timestamp"].sort_values().diff().dropna() / duration - 1
        intraday_missing += int(gaps.clip(lower=0).apply(int).sum())
    return missing_sessions + intraday_missing


def _stock_intraday_is_stale(latest: datetime, checked_at: datetime, interval: str) -> bool:
    eastern_now = checked_at.astimezone(ZoneInfo("America/New_York"))
    eastern_latest = latest.astimezone(ZoneInfo("America/New_York"))
    active_session = (
        _is_stock_session(eastern_now.date())
        and STOCK_MARKET_OPEN <= eastern_now.time() <= STOCK_MARKET_CLOSE
    )
    if active_session:
        if eastern_latest.date() < eastern_now.date():
            market_open = eastern_now.replace(hour=9, minute=30, second=0, microsecond=0)
            return eastern_now - market_open > INTERVAL_DURATION[interval] * 2
        return checked_at - latest > INTERVAL_DURATION[interval] * 2
    expected_session = _expected_stock_session(checked_at)
    return eastern_latest.date() < expected_session


def assess_data_quality(
    ticker: str,
    asset_type: AssetType,
    candles: pd.DataFrame,
    interval: str = "1d",
    now: datetime | None = None,
) -> DataQualityReport:
    if interval not in INTERVAL_DURATION:
        raise ValueError("interval must be one of: 1d, 4h, 1h")
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    else:
        checked_at = checked_at.astimezone(timezone.utc)

    if candles.empty:
        return DataQualityReport(
            ticker=ticker,
            asset_type=asset_type.value,
            interval=interval,
            status="invalid",
            is_eligible=False,
            candle_count=0,
            latest_timestamp=None,
            age_hours=None,
            stale=True,
            duplicate_timestamps=0,
            missing_periods=0,
            invalid_timestamps=0,
            invalid_ohlc=0,
            anomaly_count=0,
            issues=["No candle data available"],
        )

    timestamps = pd.to_datetime(candles["timestamp"], utc=True, errors="coerce")
    invalid_timestamps = int(timestamps.isna().sum())
    valid_mask = timestamps.notna()
    valid_timestamps = timestamps.loc[valid_mask].sort_values()
    duplicate_timestamps = int(valid_timestamps.duplicated().sum())
    candle_count = len(candles)

    latest_timestamp: str | None = None
    age_hours: float | None = None
    stale = True
    missing_periods = 0
    if not valid_timestamps.empty:
        latest = valid_timestamps.iloc[-1].to_pydatetime()
        latest_timestamp = latest.isoformat()
        age_hours = round(max(0.0, (checked_at - latest).total_seconds() / 3600), 2)
        if asset_type == AssetType.CRYPTO:
            stale = checked_at - latest > CRYPTO_STALE_AFTER[interval]
        elif interval == "1d":
            stale = latest.date() < _expected_stock_session(checked_at)
        else:
            stale = _stock_intraday_is_stale(latest, checked_at, interval)
        missing_periods = _missing_period_count(valid_timestamps, asset_type, interval)

    numeric = candles.loc[valid_mask, ["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric, errors="coerce"
    ).replace([float("inf"), float("-inf")], pd.NA)
    invalid_numeric = numeric.isna().any(axis=1)
    invalid_price = (numeric[["open", "high", "low", "close"]] <= 0).any(axis=1)
    invalid_volume = numeric["volume"] < 0
    invalid_range = (
        (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1))
        | (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1))
    )
    invalid_ohlc = int((invalid_numeric | invalid_price | invalid_volume | invalid_range).sum())

    anomaly_threshold = 0.75 if asset_type == AssetType.CRYPTO else 0.40
    anomaly_count = int((numeric["close"].pct_change().abs() > anomaly_threshold).sum())

    issues: list[str] = []
    if candle_count < MIN_ANALYSIS_CANDLES:
        issues.append(f"Insufficient history: {candle_count}/{MIN_ANALYSIS_CANDLES} candles")
    if invalid_timestamps:
        issues.append(f"{invalid_timestamps} invalid timestamp(s)")
    if duplicate_timestamps:
        issues.append(f"{duplicate_timestamps} duplicate timestamp(s)")
    if invalid_ohlc:
        issues.append(f"{invalid_ohlc} invalid OHLCV row(s)")
    if stale:
        issues.append("Latest candle is stale")
    if missing_periods:
        issues.append(f"{missing_periods} missing expected period(s)")
    if anomaly_count:
        issues.append(f"{anomaly_count} extreme return anomaly/anomalies")

    is_eligible = (
        candle_count >= MIN_ANALYSIS_CANDLES
        and not stale
        and invalid_timestamps == 0
        and duplicate_timestamps == 0
        and invalid_ohlc == 0
    )
    if invalid_timestamps or duplicate_timestamps or invalid_ohlc:
        status = "invalid"
    elif stale:
        status = "stale"
    elif candle_count < MIN_ANALYSIS_CANDLES:
        status = "insufficient"
    elif missing_periods or anomaly_count:
        status = "warning"
    else:
        status = "healthy"

    return DataQualityReport(
        ticker=ticker,
        asset_type=asset_type.value,
        interval=interval,
        status=status,
        is_eligible=is_eligible,
        candle_count=candle_count,
        latest_timestamp=latest_timestamp,
        age_hours=age_hours,
        stale=stale,
        duplicate_timestamps=duplicate_timestamps,
        missing_periods=missing_periods,
        invalid_timestamps=invalid_timestamps,
        invalid_ohlc=invalid_ohlc,
        anomaly_count=anomaly_count,
        issues=issues,
    )
