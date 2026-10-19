import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.market_models import (
    candle_revisions,
    candles,
    instrument_symbols,
    instruments,
    providers,
)
from app.models import AssetType
from app.postgres_store import count_candles, load_candles, upsert_candles


DATABASE_URL = os.environ.get("POSTGRES_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="POSTGRES_TEST_DATABASE_URL is not configured",
)


def _frame(
    *,
    rows: int = 1,
    start_offset: int = 0,
    provider: str = "fake",
    provider_symbol: str = "BTCUSD",
    observed_at: datetime | None = None,
    close_offset: Decimal = Decimal("0"),
) -> pd.DataFrame:
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    records = []
    for offset in range(start_offset, start_offset + rows):
        base = Decimal("100.000000000001") + Decimal(offset)
        records.append(
            {
                "timestamp": start + timedelta(days=offset),
                "open": str(base),
                "high": str(base + Decimal("10.000000000000") + close_offset),
                "low": str(base - Decimal("10.000000000000")),
                "close": str(base + Decimal("1.123456789012") + close_offset),
                "volume": "123.123456789012345678",
            }
        )
    frame = pd.DataFrame(records)
    frame.attrs.update(
        provider=provider,
        provider_symbol=provider_symbol,
        provider_observed_at=observed_at or datetime.now(timezone.utc),
        quality_status="warning",
        quality_flags=["deterministic-fixture"],
        source_event_id=f"{provider}-fixture",
    )
    return frame


async def _reset_database(engine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE market_data.providers, market_data.venues, "
                "market_data.instruments, market_data.instrument_symbols, "
                "market_data.candles, market_data.candle_revisions "
                "RESTART IDENTITY CASCADE"
            )
        )


def _plan_uses_index(node: object, index_name: str) -> bool:
    if isinstance(node, dict):
        if node.get("Index Name") == index_name:
            return True
        return any(_plan_uses_index(value, index_name) for value in node.values())
    if isinstance(node, list):
        return any(_plan_uses_index(value, index_name) for value in node)
    return False


def test_concurrent_upserts_are_canonical_and_idempotent() -> None:
    async def run() -> None:
        engine = create_async_engine(DATABASE_URL)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await _reset_database(engine)
        frame = _frame(rows=40)

        async def write() -> None:
            async with sessions() as session:
                assert (
                    await upsert_candles(
                        session,
                        "BTC",
                        AssetType.CRYPTO,
                        frame,
                        "1d",
                    )
                    == 40
                )

        await asyncio.gather(*(write() for _ in range(8)))
        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(providers))
                == 1
            )
            assert (
                await session.scalar(select(func.count()).select_from(instruments))
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(instrument_symbols)
                )
                == 1
            )
            assert (
                await session.scalar(select(func.count()).select_from(candles))
                == 40
            )
            assert (
                await session.scalar(
                    select(func.max(candles.c.provider_revision))
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(candle_revisions)
                )
                == 0
            )
        await engine.dispose()

    asyncio.run(run())


def test_provider_revisions_and_freshness_are_retained() -> None:
    async def run() -> None:
        engine = create_async_engine(DATABASE_URL)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await _reset_database(engine)
        first_observation = datetime(2025, 1, 1, 10, tzinfo=timezone.utc)
        refreshed_observation = datetime(2025, 1, 1, 11, tzinfo=timezone.utc)
        revised_observation = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)

        async with sessions() as session:
            await upsert_candles(
                session,
                "BTC",
                AssetType.CRYPTO,
                _frame(observed_at=first_observation),
                "1d",
            )
        async with sessions() as session:
            await upsert_candles(
                session,
                "BTC",
                AssetType.CRYPTO,
                _frame(observed_at=refreshed_observation),
                "1d",
            )
        async with sessions() as session:
            unchanged = (
                await session.execute(
                    select(
                        candles.c.provider_revision,
                        candles.c.provider_observed_at,
                        candles.c.quality_status,
                        candles.c.quality_flags,
                    )
                )
            ).one()
            assert unchanged.provider_revision == 1
            assert unchanged.provider_observed_at == refreshed_observation
            assert unchanged.quality_status == "warning"
            assert unchanged.quality_flags == ["deterministic-fixture"]
            assert (
                await session.scalar(
                    select(func.count()).select_from(candle_revisions)
                )
                == 0
            )

        async with sessions() as session:
            await upsert_candles(
                session,
                "BTC",
                AssetType.CRYPTO,
                _frame(
                    observed_at=revised_observation,
                    close_offset=Decimal("1.000000000000"),
                ),
                "1d",
            )
        async with sessions() as session:
            current = (
                await session.execute(
                    select(
                        candles.c.close_price,
                        candles.c.provider_revision,
                        candles.c.provider_observed_at,
                    )
                )
            ).one()
            archived = (
                await session.execute(
                    select(
                        candle_revisions.c.close_price,
                        candle_revisions.c.provider_revision,
                        candle_revisions.c.provider_observed_at,
                    )
                )
            ).one()
            assert current.close_price == Decimal("102.123456789013")
            assert current.provider_revision == 2
            assert current.provider_observed_at == revised_observation
            assert archived.close_price == Decimal("101.123456789013")
            assert archived.provider_revision == 1
            assert archived.provider_observed_at == refreshed_observation
        await engine.dispose()

    asyncio.run(run())


def test_provider_aware_reads_return_one_canonical_candle() -> None:
    async def run() -> None:
        engine = create_async_engine(DATABASE_URL)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await _reset_database(engine)
        async with sessions() as session:
            await upsert_candles(
                session,
                "BTC",
                AssetType.CRYPTO,
                _frame(
                    provider="binance",
                    provider_symbol="BTCUSDT",
                    observed_at=datetime(2025, 1, 1, 10, tzinfo=timezone.utc),
                ),
                "1d",
            )
        async with sessions() as session:
            await upsert_candles(
                session,
                "BTC",
                AssetType.CRYPTO,
                _frame(
                    provider="fallback",
                    provider_symbol="BTC-USD",
                    observed_at=datetime(2025, 1, 1, 11, tzinfo=timezone.utc),
                    close_offset=Decimal("1.000000000000"),
                ),
                "1d",
            )
        async with sessions() as session:
            loaded = await load_candles(session, "BTC", "1d")
            assert len(loaded) == 1
            assert await count_candles(session, "BTC", "1d") == 1
            assert loaded.iloc[0]["close"] == pytest.approx(102.123456789013)
            assert (
                await session.scalar(select(func.count()).select_from(instruments))
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(instrument_symbols)
                )
                == 2
            )
        await engine.dispose()

    asyncio.run(run())


def test_provider_symbol_cannot_be_remapped_to_another_instrument() -> None:
    async def run() -> None:
        engine = create_async_engine(DATABASE_URL)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await _reset_database(engine)
        async with sessions() as session:
            await upsert_candles(
                session,
                "BTC",
                AssetType.CRYPTO,
                _frame(provider="binance", provider_symbol="BTCUSDT"),
                "1d",
            )
        async with sessions() as session:
            with pytest.raises(ValueError, match="already mapped"):
                await upsert_candles(
                    session,
                    "ETH",
                    AssetType.CRYPTO,
                    _frame(provider="binance", provider_symbol="BTCUSDT"),
                    "1d",
                )
        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(instruments))
                == 1
            )
            assert (
                await session.scalar(select(func.count()).select_from(candles))
                == 1
            )
        await engine.dispose()

    asyncio.run(run())


def test_incremental_upsert_preserves_existing_history() -> None:
    async def run() -> None:
        engine = create_async_engine(DATABASE_URL)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await _reset_database(engine)
        async with sessions() as session:
            await upsert_candles(
                session,
                "BTC",
                AssetType.CRYPTO,
                _frame(rows=4),
                "1d",
            )
        async with sessions() as session:
            await upsert_candles(
                session,
                "BTC",
                AssetType.CRYPTO,
                _frame(
                    rows=2,
                    start_offset=3,
                    observed_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                    close_offset=Decimal("1.000000000000"),
                ),
                "1d",
            )
        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(candles))
                == 5
            )
            assert (
                await session.scalar(
                    select(func.min(candles.c.bucket_start))
                )
                == datetime(2020, 1, 1, tzinfo=timezone.utc)
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(candle_revisions)
                )
                == 1
            )
        await engine.dispose()

    asyncio.run(run())


def test_large_range_query_uses_history_index() -> None:
    async def run() -> None:
        engine = create_async_engine(DATABASE_URL)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await _reset_database(engine)
        async with sessions() as session:
            await upsert_candles(
                session,
                "BTC",
                AssetType.CRYPTO,
                _frame(rows=1_500),
                "1d",
            )
        async with sessions() as session:
            instrument_id = await session.scalar(select(instruments.c.id))
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            plan = await session.scalar(
                text(
                    "EXPLAIN (FORMAT JSON) "
                    "SELECT bucket_start, close_price "
                    "FROM market_data.candles "
                    "WHERE instrument_id = :instrument_id "
                    "AND bar_interval = '1d' "
                    "AND bucket_start >= '2022-01-01T00:00:00Z' "
                    "ORDER BY bucket_start DESC"
                ),
                {"instrument_id": instrument_id},
            )
            assert _plan_uses_index(
                plan,
                "idx_candles_instrument_interval_time",
            )
        await engine.dispose()

    asyncio.run(run())
