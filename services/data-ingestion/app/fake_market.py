"""Deterministic market data used by the isolated sandbox profile."""

import hashlib
import math
from datetime import datetime, time, timezone

import pandas as pd

from app.models import AssetType


STOCK_NAMES = {
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "SPY": "SPDR S&P 500 ETF Trust",
}

CRYPTO_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
}


def _seed(ticker: str) -> int:
    return int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16)


def _stock_timestamps(interval: str, periods: int) -> pd.DatetimeIndex:
    sessions = pd.bdate_range(
        end=pd.Timestamp.now(tz="America/New_York").normalize(),
        periods=periods if interval == "1d" else max(120, periods // 2),
    )
    if interval == "1d":
        return sessions.tz_convert("UTC")

    hours = (time(9, 30), time(13, 30)) if interval == "4h" else tuple(
        time(hour, 30) for hour in range(9, 16)
    )
    timestamps = [
        session.replace(hour=value.hour, minute=value.minute).tz_convert("UTC")
        for session in sessions
        for value in hours
    ]
    return pd.DatetimeIndex(timestamps[-periods:])


def _crypto_timestamps(interval: str, periods: int) -> pd.DatetimeIndex:
    frequency = {"1d": "1D", "4h": "4h", "1h": "1h"}[interval]
    return pd.date_range(
        end=pd.Timestamp.now(tz="UTC").floor(frequency),
        periods=periods,
        freq=frequency,
    )


def fake_candles(
    ticker: str,
    asset_type: AssetType,
    interval: str,
    periods: int = 400,
) -> pd.DataFrame:
    normalized = ticker.strip().upper()
    timestamps = (
        _crypto_timestamps(interval, periods)
        if asset_type == AssetType.CRYPTO
        else _stock_timestamps(interval, periods)
    )
    seed = _seed(normalized)
    base_price = 40.0 + (seed % 20_000) / 100
    rows = []
    for index, timestamp in enumerate(timestamps):
        trend = index * (0.015 + (seed % 7) / 1_000)
        cycle = math.sin((index + seed % 17) / 9) * 1.5
        close = base_price + trend + cycle
        open_price = close - math.sin((index + 3) / 5) * 0.4
        high = max(open_price, close) + 0.75
        low = min(open_price, close) - 0.75
        rows.append(
            {
                "timestamp": timestamp,
                "open": round(open_price, 6),
                "high": round(high, 6),
                "low": round(low, 6),
                "close": round(close, 6),
                "volume": float(100_000 + seed % 50_000 + index * 100),
            }
        )
    return pd.DataFrame(rows)


def fake_quote(ticker: str, asset_type: AssetType) -> dict[str, object]:
    frame = fake_candles(ticker, asset_type, "1d")
    previous = float(frame.iloc[-2]["close"])
    latest = frame.iloc[-1]
    normalized = ticker.strip().upper()
    names = CRYPTO_NAMES if asset_type == AssetType.CRYPTO else STOCK_NAMES
    return {
        "ticker": normalized,
        "name": names.get(normalized, f"Sandbox {normalized}"),
        "asset_type": asset_type.value,
        "price": float(latest["close"]),
        "change_pct": round((float(latest["close"]) - previous) / previous * 100, 2),
        "volume": float(latest["volume"]),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
