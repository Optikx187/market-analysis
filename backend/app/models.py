import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Boolean,
    Enum as SAEnum,
    Text,
)
from sqlalchemy.sql import func
import enum

from app.database import Base


class AssetType(str, enum.Enum):
    CRYPTO = "crypto"
    STOCK = "stock"


class SignalDirection(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


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

    __table_args__ = (
        {"sqlite_autoincrement": True},
    )


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    direction = Column(SAEnum(SignalDirection), nullable=False)
    trigger_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    target_price = Column(Float, nullable=False)
    reason = Column(Text, nullable=True)
    risk_reward = Column(Float, nullable=True)
    atr_value = Column(Float, nullable=True)
    rsi_value = Column(Float, nullable=True)
    suppressed = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    direction = Column(SAEnum(SignalDirection), nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    target_price = Column(Float, nullable=False)
    trailing_stop = Column(Float, nullable=True)
    status = Column(SAEnum(TradeStatus), default=TradeStatus.OPEN)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    opened_at = Column(DateTime, server_default=func.now())
    closed_at = Column(DateTime, nullable=True)


class Portfolio(Base):
    __tablename__ = "portfolio"

    id = Column(Integer, primary_key=True, autoincrement=True)
    balance = Column(Float, nullable=False)
    equity = Column(Float, nullable=False)
    total_pnl = Column(Float, default=0.0)
    win_count = Column(Integer, default=0)
    loss_count = Column(Integer, default=0)
    max_drawdown = Column(Float, default=0.0)
    peak_equity = Column(Float, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    equity = Column(Float, nullable=False)
    balance = Column(Float, nullable=False)
    timestamp = Column(DateTime, server_default=func.now())


class SystemLog(Base):
    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    level = Column(String(10), default="INFO")
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
