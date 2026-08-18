import enum
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, Enum as SAEnum, Text, ForeignKey,
)
from sqlalchemy.sql import func

from app.database import Base


class SignalDirection(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    SUPPRESSED = "SUPPRESSED"


class TradeStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class ExecutionKind(str, enum.Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


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
    asset_type = Column(String(20), nullable=False, default="stock")
    sector = Column(String(100), nullable=False, default="Unclassified")
    market_regime = Column(String(20), nullable=False, default="unknown")
    volatility_regime = Column(String(20), nullable=False, default="unknown")
    breadth_regime = Column(String(20), nullable=False, default="unknown")
    risk_regime = Column(String(20), nullable=False, default="unknown")
    regime_label = Column(String(200), nullable=False, default="Unknown")
    timeframe_agreement = Column(Float, nullable=True)
    strategy_name = Column(String(100), nullable=True)
    strategy_version = Column(String(50), nullable=True)
    timeframe = Column(String(20), nullable=True)
    signal_confidence = Column(Float, nullable=True)
    signal_context_json = Column(Text, nullable=True)
    execution_context_json = Column(Text, nullable=True)
    planned_entry_price = Column(Float, nullable=True)
    planned_exit_price = Column(Float, nullable=True)
    planned_quantity = Column(Float, nullable=True)
    entry_fees = Column(Float, nullable=False, default=0.0)
    entry_slippage = Column(Float, nullable=False, default=0.0)
    entry_costs_allocated = Column(Float, nullable=False, default=0.0)
    exit_fees_total = Column(Float, nullable=False, default=0.0)
    exit_slippage_total = Column(Float, nullable=False, default=0.0)
    costs_total = Column(Float, nullable=False, default=0.0)
    realized_quantity = Column(Float, nullable=False, default=0.0)
    gross_pnl = Column(Float, nullable=True)
    mfe_usd = Column(Float, nullable=True)
    mae_usd = Column(Float, nullable=True)
    mfe_pct = Column(Float, nullable=True)
    mae_pct = Column(Float, nullable=True)
    excursion_status = Column(String(20), nullable=False, default="not_calculated")
    status = Column(SAEnum(TradeStatus), default=TradeStatus.OPEN)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    opened_at = Column(DateTime, server_default=func.now())
    closed_at = Column(DateTime, nullable=True)

    @property
    def remaining_quantity(self) -> float:
        return round((self.quantity or 0.0) - (self.realized_quantity or 0.0), 8)


class TradeExecution(Base):
    """Individual entry/exit fill so fees and partial P&L stay auditable."""

    __tablename__ = "trade_executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False, index=True)
    kind = Column(SAEnum(ExecutionKind), nullable=False)
    price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    fees = Column(Float, nullable=False, default=0.0)
    slippage = Column(Float, nullable=False, default=0.0)
    entry_costs_allocated = Column(Float, nullable=False, default=0.0)
    gross_pnl = Column(Float, nullable=True)
    net_pnl = Column(Float, nullable=True)
    note = Column(Text, nullable=True)
    executed_at = Column(DateTime, server_default=func.now())


class TradeJournal(Base):
    """One automated post-trade journal entry per fully closed trade."""

    __tablename__ = "trade_journals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False, unique=True, index=True)
    ticker = Column(String(20), nullable=False, index=True)
    strategy_name = Column(String(100), nullable=True)
    outcome = Column(String(20), nullable=False)
    net_pnl = Column(Float, nullable=False)
    summary = Column(Text, nullable=False)
    journal_json = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


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
    approved = Column(Boolean, default=True)
    message = Column(Text, nullable=True)
    risk_decision_json = Column(Text, nullable=True)
    market_regime = Column(String(20), nullable=False, default="unknown")
    volatility_regime = Column(String(20), nullable=False, default="unknown")
    breadth_regime = Column(String(20), nullable=False, default="unknown")
    risk_regime = Column(String(20), nullable=False, default="unknown")
    regime_label = Column(String(200), nullable=False, default="Unknown")
    timeframe_agreement = Column(Float, nullable=True)
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
