import enum

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.sql import func

from app.database import Base


class AssetType(str, enum.Enum):
    CRYPTO = "crypto"
    STOCK = "stock"


class Asset(Base):
    __tablename__ = "assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    asset_type = Column(SAEnum(AssetType), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class Candle(Base):
    __tablename__ = "candles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    interval = Column(String(10), default="1d")
    provider = Column(String(30), nullable=False, default="legacy")
    provider_symbol = Column(String(50), nullable=False, default="")
    provider_observed_at = Column(DateTime, nullable=True)
    first_ingested_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    ingested_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    provider_revision = Column(Integer, nullable=False, default=1)
    content_checksum = Column(String(64), nullable=True)


class CandleRevision(Base):
    __tablename__ = "candle_revisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    candle_id = Column(Integer, nullable=False, index=True)
    ticker = Column(String(20), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False)
    interval = Column(String(10), nullable=False)
    provider = Column(String(30), nullable=False)
    provider_symbol = Column(String(50), nullable=False)
    provider_observed_at = Column(DateTime, nullable=True)
    ingested_at = Column(DateTime, nullable=True)
    provider_revision = Column(Integer, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    content_checksum = Column(String(64), nullable=False)
    superseded_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    condition = Column(String(10), nullable=False)  # "above" or "below"
    threshold = Column(Float, nullable=False)
    triggered = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    triggered_at = Column(DateTime, nullable=True)
