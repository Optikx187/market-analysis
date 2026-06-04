"""Data ingestion: fetch historical OHLCV data from yfinance and Binance."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
import pandas as pd
import yfinance as yf
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Asset, AssetType, Candle

logger = logging.getLogger(__name__)

BINANCE_REST_URL = "https://api.binance.com/api/v3"
CRYPTO_BINANCE_MAP = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}


def get_binance_symbol(ticker: str) -> str:
    base = ticker.upper().replace("-USD", "").replace("USD", "")
    return CRYPTO_BINANCE_MAP.get(base, f"{base}USDT")


async def fetch_historical_yfinance(
    ticker: str, period: str = "1y", interval: str = "1d",
) -> pd.DataFrame:
    loop = asyncio.get_event_loop()
    t = yf.Ticker(ticker)
    df = await loop.run_in_executor(
        None, lambda: t.history(period=period, interval=interval),
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
    symbol: str, interval: str = "1d", limit: int = 365,
) -> pd.DataFrame:
    url = f"{BINANCE_REST_URL}/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
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


async def fetch_historical(ticker: str, asset_type: AssetType) -> pd.DataFrame:
    if asset_type == AssetType.CRYPTO:
        symbol = get_binance_symbol(ticker)
        try:
            df = await fetch_historical_binance(symbol, "1d", 365)
            if not df.empty:
                return df
        except Exception as e:
            logger.warning(f"Binance failed for {symbol}, fallback to yfinance: {e}")
        yf_ticker = f"{ticker}-USD" if not ticker.endswith("-USD") else ticker
        return await fetch_historical_yfinance(yf_ticker, "1y", "1d")
    return await fetch_historical_yfinance(ticker, "1y", "1d")


async def store_candles(
    db: AsyncSession, ticker: str, df: pd.DataFrame, interval: str = "1d",
):
    await db.execute(
        delete(Candle).where(Candle.ticker == ticker, Candle.interval == interval),
    )
    for _, row in df.iterrows():
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


async def load_candles(
    db: AsyncSession, ticker: str, interval: str = "1d",
) -> pd.DataFrame:
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


async def refresh_asset_data(ticker: str, asset_type: AssetType):
    df = await fetch_historical(ticker, asset_type)
    if df.empty:
        logger.warning(f"No data for {ticker}")
        return
    async with async_session() as db:
        await store_candles(db, ticker, df)
    logger.info(f"Stored {len(df)} candles for {ticker}")
