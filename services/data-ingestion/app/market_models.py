from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func


metadata = MetaData(schema="market_data")

providers = Table(
    "providers",
    metadata,
    Column("id", SmallInteger, primary_key=True, autoincrement=True),
    Column("provider_key", Text, nullable=False, unique=True),
    Column("display_name", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

venues = Table(
    "venues",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("venue_key", Text, nullable=False, unique=True),
    Column("display_name", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

instruments = Table(
    "instruments",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("instrument_key", Text, nullable=False, unique=True),
    Column("display_name", Text, nullable=False),
    Column("asset_class", Text, nullable=False),
    Column("base_asset", Text, nullable=False),
    Column("quote_asset", Text),
    Column("venue_id", UUID(as_uuid=True), ForeignKey("market_data.venues.id")),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("asset_class IN ('stock', 'crypto')"),
)

instrument_symbols = Table(
    "instrument_symbols",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "instrument_id",
        UUID(as_uuid=True),
        ForeignKey("market_data.instruments.id"),
        nullable=False,
    ),
    Column(
        "provider_id",
        SmallInteger,
        ForeignKey("market_data.providers.id"),
        nullable=False,
    ),
    Column("provider_symbol", Text, nullable=False),
    Column("normalized_symbol", Text, nullable=False),
    Column("valid_from", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("valid_to", DateTime(timezone=True)),
)

candles = Table(
    "candles",
    metadata,
    Column(
        "provider_id",
        SmallInteger,
        ForeignKey("market_data.providers.id"),
        primary_key=True,
    ),
    Column(
        "instrument_id",
        UUID(as_uuid=True),
        ForeignKey("market_data.instruments.id"),
        primary_key=True,
    ),
    Column("bar_interval", Text, primary_key=True),
    Column("bucket_start", DateTime(timezone=True), primary_key=True),
    Column("open_price", Numeric(30, 12), nullable=False),
    Column("high_price", Numeric(30, 12), nullable=False),
    Column("low_price", Numeric(30, 12), nullable=False),
    Column("close_price", Numeric(30, 12), nullable=False),
    Column("volume", Numeric(38, 18), nullable=False),
    Column("provider_observed_at", DateTime(timezone=True), nullable=False),
    Column("first_ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("provider_revision", Integer, nullable=False, server_default="1"),
    Column("is_complete", Boolean, nullable=False, server_default="true"),
    Column("quality_status", Text, nullable=False, server_default="valid"),
    Column("quality_flags", JSON, nullable=False, server_default="[]"),
    Column("source_event_id", Text),
    Column("content_checksum", String(64), nullable=False),
    CheckConstraint("bar_interval IN ('1m', '5m', '15m', '1h', '4h', '1d', '1w')"),
    CheckConstraint("quality_status IN ('valid', 'warning', 'blocked')"),
)

candle_revisions = Table(
    "candle_revisions",
    metadata,
    Column("provider_id", SmallInteger, primary_key=True),
    Column("instrument_id", UUID(as_uuid=True), primary_key=True),
    Column("bar_interval", Text, primary_key=True),
    Column("bucket_start", DateTime(timezone=True), primary_key=True),
    Column("provider_revision", Integer, primary_key=True),
    Column("open_price", Numeric(30, 12), nullable=False),
    Column("high_price", Numeric(30, 12), nullable=False),
    Column("low_price", Numeric(30, 12), nullable=False),
    Column("close_price", Numeric(30, 12), nullable=False),
    Column("volume", Numeric(38, 18), nullable=False),
    Column("provider_observed_at", DateTime(timezone=True), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False),
    Column("quality_status", Text, nullable=False),
    Column("quality_flags", JSON, nullable=False),
    Column("source_event_id", Text),
    Column("content_checksum", String(64), nullable=False),
    Column("superseded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

Index(
    "idx_instrument_symbols_instrument",
    instrument_symbols.c.instrument_id,
    instrument_symbols.c.provider_id,
    instrument_symbols.c.valid_from.desc(),
)
Index(
    "idx_candles_instrument_interval_time",
    candles.c.instrument_id,
    candles.c.bar_interval,
    candles.c.bucket_start.desc(),
)
Index(
    "idx_candles_provider_ingested",
    candles.c.provider_id,
    candles.c.ingested_at.desc(),
)
