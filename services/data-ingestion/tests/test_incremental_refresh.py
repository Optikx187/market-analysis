import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pandas as pd
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import ingestion
from app.config import settings
from app.models import AssetType, Candle, CandleRevision

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _frame(
    interval: str,
    *,
    rows: int = 220,
    now: datetime | None = None,
    close_offset: float = 0,
) -> pd.DataFrame:
    current = now or datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    frequency = {"1d": "1D", "4h": "4h", "1h": "1h"}[interval]
    timestamps = pd.date_range(end=current, periods=rows, freq=frequency)
    values = pd.Series(range(rows), dtype=float)
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": 100 + values,
            "high": 102 + values + close_offset,
            "low": 99 + values,
            "close": 101 + values + close_offset,
            "volume": 1_000 + values,
        }
    )
    frame.attrs.update(
        {
            "provider": "fixture",
            "provider_symbol": "BTCUSD",
            "provider_observed_at": current,
        }
    )
    return frame


def _database(tmp_path: Path):
    path = tmp_path / "incremental.db"
    url = f"sqlite+aiosqlite:///{path}"
    original_url = settings.DATABASE_URL
    original_backend = settings.MARKET_DATA_STORAGE_BACKEND
    settings.DATABASE_URL = url
    settings.MARKET_DATA_STORAGE_BACKEND = "sqlite-transition"
    try:
        command.upgrade(
            Config(str(SERVICE_ROOT / "alembic.ini")),
            "head",
        )
    finally:
        settings.DATABASE_URL = original_url
    engine = create_async_engine(url)
    return (
        engine,
        async_sessionmaker(engine, expire_on_commit=False),
        original_backend,
    )


def test_refresh_plan_overlaps_recent_data_and_bounds_stale_ranges() -> None:
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    recent = ingestion.plan_refresh(
        now - timedelta(hours=1),
        "1h",
        now=now,
    )
    stale = ingestion.plan_refresh(
        now - timedelta(days=365),
        "1h",
        now=now,
    )
    future = ingestion.plan_refresh(
        now + timedelta(days=1),
        "1h",
        now=now,
    )
    bootstrap = ingestion.plan_refresh(None, "1d", now=now)
    missing = ingestion.plan_refresh(
        now - timedelta(hours=1),
        "1h",
        now=now,
        timestamps=[
            now - timedelta(hours=5),
            now - timedelta(hours=4),
            now - timedelta(hours=2),
            now - timedelta(hours=1),
        ],
    )

    assert recent.request_start == now - timedelta(hours=3)
    assert stale.request_start == now - timedelta(
        hours=settings.MARKET_DATA_MAX_BACKFILL_BARS
    )
    assert future.request_start == now - timedelta(hours=2)
    assert bootstrap.request_start is None
    assert bootstrap.bootstrap is True
    assert missing.request_start == now - timedelta(hours=5)

    monday = datetime(2026, 7, 13, 20, tzinfo=timezone.utc)
    stock_weekend = ingestion.plan_refresh(
        monday,
        "1d",
        now=monday,
        timestamps=[
            datetime(2026, 7, 10, 20, tzinfo=timezone.utc),
            monday,
        ],
        asset_type=AssetType.STOCK,
    )
    assert stock_weekend.request_start == monday - timedelta(days=2)


def test_sqlite_overlap_updates_in_place_and_archives_revision(
    tmp_path: Path,
) -> None:
    engine, sessions, original_backend = _database(tmp_path)

    async def run() -> None:
        first = _frame("1d", rows=3)
        revised = _frame("1d", rows=1, close_offset=1)
        revised["timestamp"] = [first.iloc[-1]["timestamp"]]
        async with sessions() as session:
            await ingestion.store_candles(
                session,
                "BTC",
                first,
                "1d",
                asset_type=AssetType.CRYPTO,
            )
        async with sessions() as session:
            first_ingested_at = (
                await session.scalar(
                    select(Candle.first_ingested_at)
                    .where(Candle.ticker == "BTC")
                    .order_by(Candle.timestamp.desc())
                    .limit(1)
                )
            )
        async with sessions() as session:
            await ingestion.store_candles(
                session,
                "BTC",
                revised,
                "1d",
                asset_type=AssetType.CRYPTO,
            )
        async with sessions() as session:
            assert await session.scalar(
                select(func.count()).select_from(Candle)
            ) == 3
            assert await session.scalar(
                select(func.count()).select_from(CandleRevision)
            ) == 1
            current = (
                await session.execute(
                    select(Candle)
                    .where(Candle.ticker == "BTC")
                    .order_by(Candle.timestamp.desc())
                    .limit(1)
                )
            ).scalar_one()
            assert current.provider_revision == 2
            assert current.first_ingested_at == first_ingested_at
            lineage = await ingestion.load_candle_lineage(
                session,
                "BTC",
                "1d",
            )
            assert lineage[0]["provider"] == "fixture"
            assert lineage[0]["provider_revision"] == 2
            assert lineage[0]["archived_revisions"] == 1

    try:
        asyncio.run(run())
    finally:
        settings.MARKET_DATA_STORAGE_BACKEND = original_backend
        asyncio.run(engine.dispose())


def test_concurrent_refreshes_share_provider_calls_and_remain_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, sessions, original_backend = _database(tmp_path)
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)

    async def fetch(
        ticker: str,
        asset_type: AssetType,
        interval: str,
        start_after: datetime | None = None,
    ) -> pd.DataFrame:
        assert ticker == "BTC"
        assert asset_type == AssetType.CRYPTO
        frame = _frame(interval, now=now)
        if start_after is not None:
            frame = frame[
                pd.to_datetime(frame["timestamp"], utc=True)
                >= pd.Timestamp(start_after)
            ].copy()
            frame.attrs.update(
                {
                    "provider": "fixture",
                    "provider_symbol": "BTCUSD",
                    "provider_observed_at": now,
                }
            )
        await asyncio.sleep(0.01)
        return frame

    fetch_mock = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(ingestion, "fetch_historical", fetch_mock)
    monkeypatch.setattr(ingestion, "async_session", sessions)
    ingestion._refresh_tasks.clear()

    async def run() -> None:
        first, duplicate = await asyncio.gather(
            ingestion.refresh_asset_data(
                "BTC",
                AssetType.CRYPTO,
                now=now,
            ),
            ingestion.refresh_asset_data(
                "BTC",
                AssetType.CRYPTO,
                now=now,
            ),
        )
        assert first == duplicate
        assert first["status"] == "success"
        assert fetch_mock.await_count == 3
        for detail in first["intervals"].values():
            assert detail["canonical_count"] == 220
            assert detail["quality"]["candle_count"] == 220
            assert detail["quality"]["missing_periods"] == 0
            assert detail["quality"]["is_eligible"] is True

        second = await ingestion.refresh_asset_data(
            "BTC",
            AssetType.CRYPTO,
            now=now + timedelta(hours=1),
        )
        assert second["status"] == "success"
        assert fetch_mock.await_count == 6
        assert all(
            call.kwargs["start_after"] is not None
            for call in fetch_mock.await_args_list[-3:]
        )
        async with sessions() as session:
            counts = {
                interval: await ingestion.candle_count(
                    session,
                    "BTC",
                    interval,
                )
                for interval in ("1d", "4h", "1h")
            }
            assert counts == {"1d": 220, "4h": 220, "1h": 220}

    try:
        asyncio.run(run())
    finally:
        settings.MARKET_DATA_STORAGE_BACKEND = original_backend
        ingestion._refresh_tasks.clear()
        asyncio.run(engine.dispose())
