import hashlib
import secrets
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, localcontext

import pandas as pd
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.market_models import (
    candles,
    instrument_symbols,
    instruments,
    providers,
    venues,
)
from app.models import AssetType


PRICE_QUANTUM = Decimal("0.000000000001")
QUANTITY_QUANTUM = Decimal("0.000000000000000001")


def uuid7() -> uuid.UUID:
    timestamp_ms = int(time.time() * 1000)
    random_bits = secrets.randbits(74)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0x2 << 62
    value |= random_bits & ((1 << 62) - 1)
    return uuid.UUID(int=value)


def canonical_instrument_key(ticker: str, asset_type: AssetType) -> str:
    normalized = ticker.strip().upper().replace("-USD", "")
    if asset_type == AssetType.CRYPTO:
        return f"crypto:{normalized}/USD"
    return f"stock:US:{normalized}"


def _decimal(value: object, quantum: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 60
        return Decimal(str(value)).quantize(quantum)


def _checksum(row: pd.Series) -> str:
    payload = [
        format(_decimal(row[column], PRICE_QUANTUM), "f")
        for column in ("open", "high", "low", "close")
    ]
    payload.append(format(_decimal(row["volume"], QUANTITY_QUANTUM), "f"))
    return hashlib.sha256("|".join(payload).encode()).hexdigest()


async def _provider_id(
    db: AsyncSession,
    provider_key: str,
) -> int:
    statement = (
        insert(providers)
        .values(provider_key=provider_key, display_name=provider_key.title())
        .on_conflict_do_update(
            index_elements=[providers.c.provider_key],
            set_={"display_name": provider_key.title()},
        )
        .returning(providers.c.id)
    )
    return int((await db.execute(statement)).scalar_one())


async def _instrument_id(
    db: AsyncSession,
    ticker: str,
    asset_type: AssetType,
    provider_id: int,
    provider_symbol: str,
) -> uuid.UUID:
    normalized = ticker.strip().upper().replace("-USD", "")
    venue_key = "24x7" if asset_type == AssetType.CRYPTO else "US"
    venue_statement = (
        insert(venues)
        .values(id=uuid7(), venue_key=venue_key, display_name=venue_key)
        .on_conflict_do_update(
            index_elements=[venues.c.venue_key],
            set_={"display_name": venue_key},
        )
        .returning(venues.c.id)
    )
    venue_id = (await db.execute(venue_statement)).scalar_one()
    instrument_key = canonical_instrument_key(normalized, asset_type)
    instrument_statement = (
        insert(instruments)
        .values(
            id=uuid7(),
            instrument_key=instrument_key,
            display_name=normalized,
            asset_class=asset_type.value,
            base_asset=normalized,
            quote_asset="USD",
            venue_id=venue_id,
        )
        .on_conflict_do_update(
            index_elements=[instruments.c.instrument_key],
            set_={
                "display_name": normalized,
                "is_active": True,
                "updated_at": func.now(),
            },
        )
        .returning(instruments.c.id)
    )
    instrument_id = (await db.execute(instrument_statement)).scalar_one()
    symbol_statement = (
        insert(instrument_symbols)
        .values(
            id=uuid7(),
            instrument_id=instrument_id,
            provider_id=provider_id,
            provider_symbol=provider_symbol,
            normalized_symbol=normalized,
        )
        .on_conflict_do_update(
            index_elements=[
                instrument_symbols.c.provider_id,
                instrument_symbols.c.provider_symbol,
            ],
            index_where=instrument_symbols.c.valid_to.is_(None),
            set_={"normalized_symbol": instrument_symbols.c.normalized_symbol},
        )
        .returning(instrument_symbols.c.instrument_id)
    )
    mapped_instrument_id = (await db.execute(symbol_statement)).scalar_one()
    if mapped_instrument_id != instrument_id:
        raise ValueError(
            f"Provider symbol {provider_symbol} is already mapped to another instrument"
        )
    return instrument_id


async def latest_timestamp(
    db: AsyncSession,
    ticker: str,
    interval: str,
) -> datetime | None:
    normalized = ticker.strip().upper().replace("-USD", "")
    statement = (
        select(func.max(candles.c.bucket_start))
        .select_from(
            candles.join(
                instrument_symbols,
                (candles.c.instrument_id == instrument_symbols.c.instrument_id)
                & (candles.c.provider_id == instrument_symbols.c.provider_id),
            )
        )
        .where(
            instrument_symbols.c.normalized_symbol == normalized,
            instrument_symbols.c.valid_to.is_(None),
            candles.c.bar_interval == interval,
        )
    )
    return (await db.execute(statement)).scalar_one_or_none()


async def count_candles(
    db: AsyncSession,
    ticker: str,
    interval: str,
) -> int:
    normalized = ticker.strip().upper().replace("-USD", "")
    statement = (
        select(func.count(func.distinct(candles.c.bucket_start)))
        .select_from(
            candles.join(
                instrument_symbols,
                (candles.c.instrument_id == instrument_symbols.c.instrument_id)
                & (candles.c.provider_id == instrument_symbols.c.provider_id),
            )
        )
        .where(
            instrument_symbols.c.normalized_symbol == normalized,
            instrument_symbols.c.valid_to.is_(None),
            candles.c.bar_interval == interval,
        )
    )
    return int((await db.execute(statement)).scalar_one())


async def upsert_candles(
    db: AsyncSession,
    ticker: str,
    asset_type: AssetType,
    frame: pd.DataFrame,
    interval: str,
) -> int:
    provider_key = str(frame.attrs.get("provider", "unknown")).lower()
    provider_symbol = str(frame.attrs.get("provider_symbol", ticker)).upper()
    observed_at = frame.attrs.get("provider_observed_at") or datetime.now(timezone.utc)
    if isinstance(observed_at, pd.Timestamp):
        observed_at = observed_at.to_pydatetime()
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    observed_at = observed_at.astimezone(timezone.utc)
    quality_status = str(frame.attrs.get("quality_status", "valid"))
    quality_flags = list(frame.attrs.get("quality_flags", []))
    provider_id = await _provider_id(db, provider_key)
    instrument_id = await _instrument_id(
        db,
        ticker,
        asset_type,
        provider_id,
        provider_symbol,
    )
    values = []
    for _, row in frame.iterrows():
        timestamp = row["timestamp"]
        if isinstance(timestamp, pd.Timestamp):
            timestamp = timestamp.to_pydatetime()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        values.append(
            {
                "provider_id": provider_id,
                "instrument_id": instrument_id,
                "bar_interval": interval,
                "bucket_start": timestamp.astimezone(timezone.utc),
                "open_price": _decimal(row["open"], PRICE_QUANTUM),
                "high_price": _decimal(row["high"], PRICE_QUANTUM),
                "low_price": _decimal(row["low"], PRICE_QUANTUM),
                "close_price": _decimal(row["close"], PRICE_QUANTUM),
                "volume": _decimal(row["volume"], QUANTITY_QUANTUM),
                "provider_observed_at": observed_at,
                "quality_status": quality_status,
                "quality_flags": quality_flags,
                "source_event_id": frame.attrs.get("source_event_id"),
                "content_checksum": _checksum(row),
            }
        )
    if not values:
        return 0
    statement = insert(candles).values(values)
    excluded = statement.excluded
    statement = statement.on_conflict_do_update(
        index_elements=[
            candles.c.provider_id,
            candles.c.instrument_id,
            candles.c.bar_interval,
            candles.c.bucket_start,
        ],
        set_={
            "open_price": excluded.open_price,
            "high_price": excluded.high_price,
            "low_price": excluded.low_price,
            "close_price": excluded.close_price,
            "volume": excluded.volume,
            "provider_observed_at": excluded.provider_observed_at,
            "ingested_at": func.now(),
            "provider_revision": case(
                (
                    candles.c.content_checksum != excluded.content_checksum,
                    candles.c.provider_revision + 1,
                ),
                else_=candles.c.provider_revision,
            ),
            "is_complete": excluded.is_complete,
            "quality_status": excluded.quality_status,
            "quality_flags": excluded.quality_flags,
            "source_event_id": excluded.source_event_id,
            "content_checksum": excluded.content_checksum,
        },
    )
    await db.execute(statement)
    await db.commit()
    return len(values)


async def load_candles(
    db: AsyncSession,
    ticker: str,
    interval: str,
) -> pd.DataFrame:
    normalized = ticker.strip().upper().replace("-USD", "")
    statement = (
        select(
            candles.c.bucket_start,
            candles.c.open_price,
            candles.c.high_price,
            candles.c.low_price,
            candles.c.close_price,
            candles.c.volume,
        )
        .select_from(
            candles.join(
                instrument_symbols,
                (candles.c.instrument_id == instrument_symbols.c.instrument_id)
                & (candles.c.provider_id == instrument_symbols.c.provider_id),
            )
        )
        .where(
            instrument_symbols.c.normalized_symbol == normalized,
            instrument_symbols.c.valid_to.is_(None),
            candles.c.bar_interval == interval,
        )
        .distinct(candles.c.bucket_start)
        .order_by(
            candles.c.bucket_start,
            candles.c.provider_observed_at.desc(),
            candles.c.ingested_at.desc(),
        )
    )
    rows = (await db.execute(statement)).all()
    return pd.DataFrame(
        [
            {
                "timestamp": row.bucket_start,
                "open": float(row.open_price),
                "high": float(row.high_price),
                "low": float(row.low_price),
                "close": float(row.close_price),
                "volume": float(row.volume),
            }
            for row in rows
        ]
    )
