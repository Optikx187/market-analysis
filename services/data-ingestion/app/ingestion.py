"""Data ingestion: fetch historical OHLCV data from yfinance, Binance, and Alpaca."""

import asyncio
import hashlib
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from itertools import pairwise

import httpx
import pandas as pd
import yfinance as yf
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.credentials import get_provider_credentials
from app.data_quality import US_EQUITY_BUSINESS_DAY, assess_data_quality
from app.database import async_session
from app.fake_market import fake_candles
from app.models import Asset, AssetType, Candle, CandleRevision
from app.postgres_store import (
    count_candles as count_postgres_candles,
)
from app.postgres_store import (
    latest_timestamp as latest_postgres_timestamp,
)
from app.postgres_store import (
    load_candle_lineage as load_postgres_candle_lineage,
)
from app.postgres_store import (
    load_candles as load_postgres_candles,
)
from app.postgres_store import (
    recent_timestamps as recent_postgres_timestamps,
)
from app.postgres_store import (
    upsert_candles,
)

logger = logging.getLogger(__name__)

CRYPTO_BINANCE_MAP = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
INTERVAL_DURATION = {
    "1d": timedelta(days=1),
    "4h": timedelta(hours=4),
    "1h": timedelta(hours=1),
}
_refresh_tasks: dict[str, asyncio.Task[dict[str, object]]] = {}
_store_locks: dict[tuple[str, str], asyncio.Lock] = {}

CRYPTO_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "BNB": "Binance Coin",
    "SOL": "Solana",
    "XRP": "Ripple",
    "ADA": "Cardano",
    "DOGE": "Dogecoin",
    "DOT": "Polkadot",
    "AVAX": "Avalanche",
    "MATIC": "Polygon",
    "LINK": "Chainlink",
    "UNI": "Uniswap",
    "ATOM": "Cosmos",
    "LTC": "Litecoin",
    "FIL": "Filecoin",
    "NEAR": "NEAR Protocol",
    "APT": "Aptos",
    "ARB": "Arbitrum",
    "OP": "Optimism",
    "SHIB": "Shiba Inu",
    "PEPE": "Pepe",
}


@dataclass(frozen=True)
class RefreshPlan:
    interval: str
    latest_timestamp: datetime | None
    request_start: datetime | None
    overlap_bars: int
    max_backfill_bars: int
    bootstrap: bool

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("latest_timestamp", "request_start"):
            value = payload[key]
            payload[key] = value.isoformat() if value is not None else None
        return payload


def plan_refresh(
    latest_timestamp: datetime | None,
    interval: str,
    *,
    now: datetime | None = None,
    timestamps: list[datetime] | None = None,
    asset_type: AssetType = AssetType.CRYPTO,
) -> RefreshPlan:
    if interval not in INTERVAL_DURATION:
        raise ValueError("interval must be one of: 1d, 4h, 1h")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    if latest_timestamp is None:
        return RefreshPlan(
            interval=interval,
            latest_timestamp=None,
            request_start=None,
            overlap_bars=settings.MARKET_DATA_OVERLAP_BARS,
            max_backfill_bars=settings.MARKET_DATA_MAX_BACKFILL_BARS,
            bootstrap=True,
        )
    latest = latest_timestamp
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    latest = min(latest.astimezone(timezone.utc), current)
    duration = INTERVAL_DURATION[interval]
    overlap_start = latest - (
        duration * settings.MARKET_DATA_OVERLAP_BARS
    )
    bounded_start = current - (
        duration * settings.MARKET_DATA_MAX_BACKFILL_BARS
    )
    missing_start = _earliest_missing_timestamp(
        timestamps or [],
        interval,
        asset_type,
    )
    if missing_start is not None:
        overlap_start = min(
            overlap_start,
            missing_start
            - duration * settings.MARKET_DATA_OVERLAP_BARS,
        )
    return RefreshPlan(
        interval=interval,
        latest_timestamp=latest_timestamp,
        request_start=max(overlap_start, bounded_start),
        overlap_bars=settings.MARKET_DATA_OVERLAP_BARS,
        max_backfill_bars=settings.MARKET_DATA_MAX_BACKFILL_BARS,
        bootstrap=False,
    )


def _earliest_missing_timestamp(
    timestamps: list[datetime],
    interval: str,
    asset_type: AssetType,
) -> datetime | None:
    if len(timestamps) < 2:
        return None
    ordered = pd.DatetimeIndex(
        pd.to_datetime(timestamps, utc=True)
    ).unique().sort_values()
    duration = INTERVAL_DURATION[interval]
    if asset_type == AssetType.CRYPTO:
        for previous, current in pairwise(ordered):
            if current - previous > duration * 1.5:
                return (previous + duration).to_pydatetime()
        return None
    local = ordered.tz_convert("America/New_York")
    dates = pd.DatetimeIndex(local.normalize()).unique().sort_values()
    expected = pd.date_range(
        start=dates[0],
        end=dates[-1],
        freq=US_EQUITY_BUSINESS_DAY,
        tz="America/New_York",
    )
    missing_sessions = expected.difference(dates)
    candidates: list[datetime] = [
        value.replace(hour=9, minute=30).tz_convert("UTC").to_pydatetime()
        for value in missing_sessions
    ]
    if interval != "1d":
        for _, group in pd.DataFrame(
            {"timestamp": local, "session": local.date}
        ).groupby("session"):
            values = pd.DatetimeIndex(
                group["timestamp"].sort_values()
            )
            for previous, current in pairwise(values):
                if current - previous > duration * 1.5:
                    candidates.append(
                        (previous + duration)
                        .tz_convert("UTC")
                        .to_pydatetime()
                    )
                    break
    return min(candidates) if candidates else None


def get_crypto_name(ticker: str) -> str:
    base = ticker.upper().replace("-USD", "").replace("USD", "")
    return CRYPTO_NAMES.get(base, base)


def get_binance_symbol(ticker: str) -> str:
    base = ticker.upper().replace("-USD", "").replace("USD", "")
    return CRYPTO_BINANCE_MAP.get(base, f"{base}USDT")


async def fetch_historical_yfinance(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
    start_after: datetime | None = None,
) -> pd.DataFrame:
    loop = asyncio.get_event_loop()
    t = yf.Ticker(ticker)
    history_kwargs = {"interval": interval}
    if start_after is None:
        history_kwargs["period"] = period
    else:
        history_kwargs["start"] = start_after
    df = await loop.run_in_executor(
        None, lambda: t.history(**history_kwargs),
    )
    if df.empty:
        return df
    df = df.reset_index()
    rename_map = {
        "Date": "timestamp", "Datetime": "timestamp",
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    }
    df = df.rename(columns=rename_map)
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    return df[[c for c in cols if c in df.columns]]


async def fetch_historical_binance(
    symbol: str,
    interval: str = "1d",
    limit: int = 1000,
    start_after: datetime | None = None,
) -> pd.DataFrame:
    url = f"{settings.BINANCE_REST_URL.rstrip('/')}/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_after is not None:
        params["startTime"] = int(start_after.timestamp() * 1000)
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    rows = []
    for k in data:
        rows.append({
            "timestamp": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
            "open": float(k[1]), "high": float(k[2]),
            "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
        })
    return pd.DataFrame(rows)


async def fetch_historical_alpaca(
    ticker: str,
    days: int = 1095,
    start_after: datetime | None = None,
) -> pd.DataFrame:
    """Fetch daily bars from Alpaca Markets for stock tickers."""
    credentials = await get_provider_credentials("alpaca")
    api_key = credentials.get("ALPACA_API_KEY")
    api_secret = credentials.get("ALPACA_API_SECRET")
    if not api_key or not api_secret:
        return pd.DataFrame()
    end = datetime.now(timezone.utc)
    start = start_after or end - timedelta(days=days)
    url = f"{settings.ALPACA_DATA_URL.rstrip('/')}/stocks/{ticker}/bars"
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }
    params = {
        "timeframe": "1Day",
        "start": start.strftime("%Y-%m-%dT00:00:00Z"),
        "end": end.strftime("%Y-%m-%dT00:00:00Z"),
        "limit": 10000,
        "adjustment": "split",
    }
    rows: list[dict] = []
    page_token = None
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            p = {**params}
            if page_token:
                p["page_token"] = page_token
            resp = await client.get(url, headers=headers, params=p)
            resp.raise_for_status()
            data = resp.json()
            for bar in data.get("bars") or []:
                rows.append({
                    "timestamp": datetime.fromisoformat(bar["t"].replace("Z", "+00:00")),
                    "open": float(bar["o"]),
                    "high": float(bar["h"]),
                    "low": float(bar["l"]),
                    "close": float(bar["c"]),
                    "volume": float(bar["v"]),
                })
            page_token = data.get("next_page_token")
            if not page_token:
                break
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _resample_four_hour(frame: pd.DataFrame, asset_type: AssetType) -> pd.DataFrame:
    if frame.empty:
        return frame
    prepared = frame.copy()
    prepared["timestamp"] = pd.to_datetime(prepared["timestamp"], errors="coerce", utc=True)
    prepared = prepared.dropna(subset=["timestamp"]).sort_values("timestamp")
    prepared["bar_timestamp"] = prepared["timestamp"]
    if asset_type == AssetType.CRYPTO:
        grouped = prepared.set_index("timestamp").resample("4h", origin="epoch")
    else:
        local = prepared["timestamp"].dt.tz_convert("America/New_York")
        prepared["session"] = local.dt.date
        prepared["bar_group"] = prepared.groupby("session").cumcount() // 4
        grouped = prepared.groupby(["session", "bar_group"], sort=True)
    result = grouped.agg({
        "bar_timestamp": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open", "high", "low", "close"])
    result = result.reset_index(drop=True).rename(columns={"bar_timestamp": "timestamp"})
    return result[["timestamp", "open", "high", "low", "close", "volume"]]


async def fetch_historical(
    ticker: str,
    asset_type: AssetType,
    interval: str = "1d",
    start_after: datetime | None = None,
) -> pd.DataFrame:
    if interval not in {"1d", "4h", "1h"}:
        raise ValueError("interval must be one of: 1d, 4h, 1h")
    if settings.MARKET_DATA_MODE.strip().lower() == "fake":
        frame = fake_candles(ticker, asset_type, interval).tail(1000)
        if start_after is not None:
            frame = frame[
                pd.to_datetime(frame["timestamp"], utc=True)
                >= pd.Timestamp(start_after).tz_convert("UTC")
            ]
        frame.attrs.update({
            "provider": "fake",
            "provider_symbol": ticker.upper(),
            "provider_observed_at": datetime.now(timezone.utc),
            "quality_status": "valid",
            "quality_flags": [],
        })
        return frame
    if asset_type == AssetType.CRYPTO:
        symbol = get_binance_symbol(ticker)
        try:
            if start_after is None:
                df = await fetch_historical_binance(symbol, interval, 1000)
            else:
                df = await fetch_historical_binance(
                    symbol,
                    interval,
                    1000,
                    start_after=start_after,
                )
            if not df.empty:
                df.attrs.update({
                    "provider": "binance",
                    "provider_symbol": symbol,
                    "provider_observed_at": datetime.now(timezone.utc),
                })
                return df
        except Exception as e:
            logger.warning(f"Binance failed for {symbol} {interval}, fallback to yfinance: {e}")
        yf_ticker = f"{ticker}-USD" if not ticker.endswith("-USD") else ticker
        if interval == "1d":
            if start_after is None:
                frame = await fetch_historical_yfinance(yf_ticker, "3y", "1d")
            else:
                frame = await fetch_historical_yfinance(
                    yf_ticker,
                    "3y",
                    "1d",
                    start_after=start_after,
                )
        else:
            if start_after is None:
                frame = await fetch_historical_yfinance(yf_ticker, "730d", "1h")
            else:
                frame = await fetch_historical_yfinance(
                    yf_ticker,
                    "730d",
                    "1h",
                    start_after=start_after,
                )
            frame = (
                _resample_four_hour(frame, asset_type)
                if interval == "4h"
                else frame.tail(1000)
            )
        frame.attrs.update({
            "provider": "yahoo",
            "provider_symbol": yf_ticker,
            "provider_observed_at": datetime.now(timezone.utc),
        })
        return frame
    if interval == "1d":
        try:
            if start_after is None:
                df = await fetch_historical_alpaca(ticker)
            else:
                df = await fetch_historical_alpaca(
                    ticker,
                    start_after=start_after,
                )
            if not df.empty:
                logger.info(f"Alpaca returned {len(df)} bars for {ticker}")
                df.attrs.update({
                    "provider": "alpaca",
                    "provider_symbol": ticker.upper(),
                    "provider_observed_at": datetime.now(timezone.utc),
                })
                return df
        except Exception as e:
            logger.warning(f"Alpaca failed for {ticker}, fallback to yfinance: {e}")
        if start_after is None:
            frame = await fetch_historical_yfinance(ticker, "3y", "1d")
        else:
            frame = await fetch_historical_yfinance(
                ticker,
                "3y",
                "1d",
                start_after=start_after,
            )
    else:
        if start_after is None:
            hourly = await fetch_historical_yfinance(ticker, "730d", "1h")
        else:
            hourly = await fetch_historical_yfinance(
                ticker,
                "730d",
                "1h",
                start_after=start_after,
            )
        frame = (
            _resample_four_hour(hourly, asset_type).tail(1000)
            if interval == "4h"
            else hourly.tail(1000)
        )
    frame.attrs.update({
        "provider": "yahoo",
        "provider_symbol": ticker.upper(),
        "provider_observed_at": datetime.now(timezone.utc),
    })
    return frame


def validate_candles_for_storage(df: pd.DataFrame) -> pd.DataFrame:
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"Candle data is missing required columns: {', '.join(missing)}")

    validated = df.copy()
    validated["timestamp"] = pd.to_datetime(validated["timestamp"], errors="coerce", utc=True)
    numeric_columns = ["open", "high", "low", "close", "volume"]
    numeric = validated[numeric_columns].apply(
        pd.to_numeric,
        errors="coerce",
    ).replace([float("inf"), float("-inf")], pd.NA)
    validated[numeric_columns] = numeric
    invalid = (
        validated["timestamp"].isna()
        | validated["timestamp"].duplicated(keep=False)
        | numeric.isna().any(axis=1)
        | (numeric[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (numeric["volume"] < 0)
        | (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1))
        | (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1))
    )
    if invalid.any():
        raise ValueError(f"Candle data contains {int(invalid.sum())} malformed row(s)")
    return validated


def _content_checksum(row: pd.Series) -> str:
    values = [
        format(float(row[column]), ".12f")
        for column in ("open", "high", "low", "close")
    ]
    values.append(format(float(row["volume"]), ".18f"))
    return hashlib.sha256("|".join(values).encode()).hexdigest()


def _naive_utc(value: datetime | pd.Timestamp | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


async def store_candles(
    db: AsyncSession,
    ticker: str,
    df: pd.DataFrame,
    interval: str = "1d",
    asset_type: AssetType | None = None,
):
    validated = validate_candles_for_storage(df)
    validated.attrs.update(df.attrs)
    if settings.MARKET_DATA_STORAGE_BACKEND.strip().lower() == "postgresql":
        for column in ("open", "high", "low", "close", "volume"):
            validated[column] = df[column].to_numpy()
        if asset_type is None:
            result = await db.execute(
                select(Asset.asset_type).where(Asset.ticker == ticker)
            )
            asset_type = result.scalar_one()
        return await upsert_candles(
            db,
            ticker,
            asset_type,
            validated,
            interval,
        )
    provider = str(validated.attrs.get("provider", "unknown")).lower()
    provider_symbol = str(
        validated.attrs.get("provider_symbol", ticker)
    ).upper()
    observed_at = _naive_utc(
        validated.attrs.get("provider_observed_at")
        or datetime.now(timezone.utc)
    )
    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    timestamps = [_naive_utc(value) for value in validated["timestamp"]]
    lock = _store_locks.setdefault(
        (ticker.strip().upper(), interval),
        asyncio.Lock(),
    )
    async with lock:
        result = await db.execute(
            select(Candle).where(
                Candle.ticker == ticker,
                Candle.interval == interval,
                Candle.timestamp.in_(timestamps),
            )
        )
        existing = {
            candle.timestamp: candle for candle in result.scalars().all()
        }
        for _, row in validated.iterrows():
            timestamp = _naive_utc(row["timestamp"])
            checksum = _content_checksum(row)
            candle = existing.get(timestamp)
            values = {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
            if candle is None:
                db.add(
                    Candle(
                        ticker=ticker,
                        timestamp=timestamp,
                        interval=interval,
                        provider=provider,
                        provider_symbol=provider_symbol,
                        provider_observed_at=observed_at,
                        first_ingested_at=ingested_at,
                        ingested_at=ingested_at,
                        provider_revision=1,
                        content_checksum=checksum,
                        **values,
                    )
                )
                continue
            current_checksum = candle.content_checksum or _content_checksum(
                pd.Series(
                    {
                        "open": candle.open,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                        "volume": candle.volume,
                    }
                )
            )
            revised = (
                current_checksum != checksum
                or candle.provider != provider
                or candle.provider_symbol != provider_symbol
            )
            if revised:
                db.add(
                    CandleRevision(
                        candle_id=candle.id,
                        ticker=candle.ticker,
                        timestamp=candle.timestamp,
                        interval=candle.interval,
                        provider=candle.provider or "legacy",
                        provider_symbol=candle.provider_symbol or candle.ticker,
                        provider_observed_at=candle.provider_observed_at,
                        ingested_at=candle.ingested_at,
                        provider_revision=candle.provider_revision or 1,
                        open=candle.open,
                        high=candle.high,
                        low=candle.low,
                        close=candle.close,
                        volume=candle.volume,
                        content_checksum=current_checksum,
                    )
                )
                candle.provider_revision = (candle.provider_revision or 1) + 1
            for key, value in values.items():
                setattr(candle, key, value)
            candle.provider = provider
            candle.provider_symbol = provider_symbol
            candle.provider_observed_at = observed_at
            candle.ingested_at = ingested_at
            candle.content_checksum = checksum
        await db.commit()
    return len(validated)


async def load_candles(
    db: AsyncSession, ticker: str, interval: str = "1d",
) -> pd.DataFrame:
    if settings.MARKET_DATA_STORAGE_BACKEND.strip().lower() == "postgresql":
        return await load_postgres_candles(db, ticker, interval)
    result = await db.execute(
        select(Candle)
        .where(Candle.ticker == ticker, Candle.interval == interval)
        .order_by(Candle.timestamp),
    )
    candles = result.scalars().all()
    if not candles:
        return pd.DataFrame()
    rows = [{
        "timestamp": c.timestamp, "open": c.open, "high": c.high,
        "low": c.low, "close": c.close, "volume": c.volume,
    } for c in candles]
    return pd.DataFrame(rows)


async def load_candle_lineage(
    db: AsyncSession,
    ticker: str,
    interval: str = "1d",
    limit: int = 100,
) -> list[dict[str, object]]:
    if settings.MARKET_DATA_STORAGE_BACKEND.strip().lower() == "postgresql":
        return await load_postgres_candle_lineage(
            db,
            ticker,
            interval,
            limit,
        )
    result = await db.execute(
        select(Candle)
        .where(Candle.ticker == ticker, Candle.interval == interval)
        .order_by(Candle.timestamp.desc())
        .limit(limit)
    )
    current = result.scalars().all()
    if not current:
        return []
    revision_result = await db.execute(
        select(
            CandleRevision.candle_id,
            func.count(CandleRevision.id),
        )
        .where(
            CandleRevision.candle_id.in_(
                [candle.id for candle in current]
            )
        )
        .group_by(CandleRevision.candle_id)
    )
    archived = dict(revision_result.all())

    def isoformat(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return [
        {
            "timestamp": candle.timestamp.isoformat(),
            "provider": candle.provider,
            "provider_symbol": candle.provider_symbol,
            "provider_observed_at": isoformat(
                candle.provider_observed_at
            ),
            "first_ingested_at": isoformat(candle.first_ingested_at),
            "ingested_at": isoformat(candle.ingested_at),
            "provider_revision": candle.provider_revision,
            "archived_revisions": archived.get(candle.id, 0),
            "content_checksum": candle.content_checksum,
        }
        for candle in current
    ]


async def latest_candle_timestamp(
    db: AsyncSession,
    ticker: str,
    interval: str,
) -> datetime | None:
    if settings.MARKET_DATA_STORAGE_BACKEND.strip().lower() == "postgresql":
        return await latest_postgres_timestamp(db, ticker, interval)
    result = await db.execute(
        select(Candle.timestamp)
        .where(Candle.ticker == ticker, Candle.interval == interval)
        .order_by(Candle.timestamp.desc())
        .limit(1)
    )
    value = result.scalar_one_or_none()
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def recent_candle_timestamps(
    db: AsyncSession,
    ticker: str,
    interval: str,
) -> list[datetime]:
    limit = (
        settings.MARKET_DATA_MAX_BACKFILL_BARS
        + settings.MARKET_DATA_OVERLAP_BARS
        + 1
    )
    if settings.MARKET_DATA_STORAGE_BACKEND.strip().lower() == "postgresql":
        return await recent_postgres_timestamps(
            db,
            ticker,
            interval,
            limit,
        )
    result = await db.execute(
        select(Candle.timestamp)
        .where(Candle.ticker == ticker, Candle.interval == interval)
        .order_by(Candle.timestamp.desc())
        .limit(limit)
    )
    return [
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
        for value in result.scalars().all()
    ]


async def candle_count(
    db: AsyncSession,
    ticker: str,
    interval: str,
) -> int:
    if settings.MARKET_DATA_STORAGE_BACKEND.strip().lower() == "postgresql":
        return await count_postgres_candles(db, ticker, interval)
    result = await db.execute(
        select(func.count(Candle.id)).where(
            Candle.ticker == ticker,
            Candle.interval == interval,
        )
    )
    return int(result.scalar_one())


async def refresh_asset_data(
    ticker: str,
    asset_type: AssetType,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    normalized = ticker.strip().upper()
    active = _refresh_tasks.get(normalized)
    if active is not None and not active.done():
        return await asyncio.shield(active)
    task = asyncio.create_task(
        _refresh_asset_data(normalized, asset_type, now=now)
    )
    _refresh_tasks[normalized] = task

    def clear(completed: asyncio.Task[dict[str, object]]) -> None:
        if _refresh_tasks.get(normalized) is completed:
            _refresh_tasks.pop(normalized, None)

    try:
        return await asyncio.shield(task)
    finally:
        if task.done():
            clear(task)
        else:
            task.add_done_callback(clear)


async def _refresh_asset_data(
    normalized: str,
    asset_type: AssetType,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    intervals: dict[str, dict[str, object]] = {}
    stored = 0
    errors = 0
    for interval in ("1d", "4h", "1h"):
        try:
            async with async_session() as db:
                timestamps = await recent_candle_timestamps(
                    db,
                    normalized,
                    interval,
                )
            latest = max(timestamps) if timestamps else None
            plan = plan_refresh(
                latest,
                interval,
                now=current,
                timestamps=timestamps,
                asset_type=asset_type,
            )
            frame = await fetch_historical(
                normalized,
                asset_type,
                interval,
                start_after=plan.request_start,
            )
            provider = str(frame.attrs.get("provider", "unknown"))
            provider_symbol = str(
                frame.attrs.get("provider_symbol", normalized)
            )
            if frame.empty:
                logger.warning(f"No {interval} data for {normalized}")
                intervals[interval] = {
                    "status": "no_data",
                    "provider": provider,
                    "provider_symbol": provider_symbol,
                    "fetched": 0,
                    "stored": 0,
                    "plan": plan.to_dict(),
                }
                continue
            async with async_session() as db:
                stored_rows = await store_candles(
                    db,
                    normalized,
                    frame,
                    interval,
                    asset_type=asset_type,
                )
                canonical = await load_candles(
                    db,
                    normalized,
                    interval,
                )
            quality = assess_data_quality(
                normalized,
                asset_type,
                canonical,
                interval,
                now=current,
            )
            stored += stored_rows
            intervals[interval] = {
                "status": "stored",
                "provider": provider,
                "provider_symbol": provider_symbol,
                "provider_observed_at": (
                    frame.attrs.get("provider_observed_at").isoformat()
                    if isinstance(
                        frame.attrs.get("provider_observed_at"),
                        datetime,
                    )
                    else None
                ),
                "fetched": len(frame),
                "stored": stored_rows,
                "canonical_count": len(canonical),
                "quality": quality.to_dict(),
                "plan": plan.to_dict(),
            }
            logger.info(
                "Stored %s %s candles for %s from %s",
                stored_rows,
                interval,
                normalized,
                provider,
            )
        except Exception as error:
            errors += 1
            intervals[interval] = {
                "status": "error",
                "error": str(error),
            }
            logger.warning(
                f"Unable to refresh {normalized} {interval}: {error}"
            )
    if stored == 0:
        logger.warning(f"No data for {normalized}")
    return {
        "ticker": normalized,
        "status": (
            "success"
            if errors == 0
            else "failed" if errors == 3 else "partial"
        ),
        "stored": stored,
        "errors": errors,
        "intervals": intervals,
    }
