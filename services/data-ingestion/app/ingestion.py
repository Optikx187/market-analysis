"""Data ingestion: fetch historical OHLCV data from yfinance, Binance, and Alpaca."""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

import httpx
import pandas as pd
import yfinance as yf
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import get_provider_credentials
from app.config import settings
from app.database import async_session
from app.fake_market import fake_candles
from app.models import Asset, AssetType, Candle
from app.postgres_store import (
    count_candles as count_postgres_candles,
    latest_timestamp as latest_postgres_timestamp,
    load_candles as load_postgres_candles,
    upsert_candles,
)

logger = logging.getLogger(__name__)

CRYPTO_BINANCE_MAP = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

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
    timestamps = []
    for value in validated["timestamp"]:
        timestamp = value.to_pydatetime() if isinstance(value, pd.Timestamp) else value
        if timestamp.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=None)
        timestamps.append(timestamp)
    await db.execute(
        delete(Candle).where(
            Candle.ticker == ticker,
            Candle.interval == interval,
            Candle.timestamp.in_(timestamps),
        ),
    )
    for _, row in validated.iterrows():
        ts = row["timestamp"]
        if isinstance(ts, pd.Timestamp):
            ts = ts.to_pydatetime()
        if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        candle = Candle(
            ticker=ticker, timestamp=ts,
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"]), interval=interval,
        )
        db.add(candle)
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


async def refresh_asset_data(ticker: str, asset_type: AssetType):
    ticker = ticker.strip()
    stored = 0
    for interval in ("1d", "4h", "1h"):
        try:
            async with async_session() as db:
                latest = await latest_candle_timestamp(db, ticker, interval)
            overlap = {
                "1d": timedelta(days=2),
                "4h": timedelta(hours=8),
                "1h": timedelta(hours=2),
            }[interval]
            start_after = latest - overlap if latest is not None else None
            frame = await fetch_historical(
                ticker,
                asset_type,
                interval,
                start_after=start_after,
            )
            if frame.empty:
                logger.warning(f"No {interval} data for {ticker}")
                continue
            async with async_session() as db:
                await store_candles(
                    db,
                    ticker,
                    frame,
                    interval,
                    asset_type=asset_type,
                )
            stored += len(frame)
            logger.info(f"Stored {len(frame)} {interval} candles for {ticker}")
        except Exception as error:
            logger.warning(f"Unable to refresh {ticker} {interval}: {error}")
    if stored == 0:
        logger.warning(f"No data for {ticker}")
