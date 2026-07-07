import enum
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Enum as SAEnum, Text
from sqlalchemy.sql import func

from app.database import Base


class SignalDirection(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    SUPPRESSED = "SUPPRESSED"


class TradeStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


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


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    direction = Column(String(20), nullable=False)
    status = Column(String(50), nullable=False)
    trigger_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    optimal_size_usd = Column(Float, nullable=True)
    kelly_pct = Column(Float, nullable=True)
    capital_overspend = Column(Boolean, default=False)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class CredentialSecret(Base):
    __tablename__ = "credential_secrets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(50), nullable=False, index=True)
    key = Column(String(100), nullable=False, unique=True, index=True)
    value = Column(Text, nullable=False)
    verified = Column(Boolean, default=False)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), nullable=False, unique=True, index=True)
    password_hash = Column(String(256), nullable=False)
    display_name = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
