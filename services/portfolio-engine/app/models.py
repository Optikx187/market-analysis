import datetime
import enum
import uuid

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Enum as SAEnum, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint, Uuid, event,
)
from sqlalchemy.orm import Session, with_loader_criteria
from sqlalchemy.sql import func

from app.auth import DEFAULT_USER_KEY, current_user_key
from app.config import settings
from app.database import Base
from app.financial import (
    MoneyValue,
    POSTGRESQL_STORAGE,
    PriceValue,
    QuantityValue,
    RatioValue,
    ScoreValue,
    uuid7,
)


PORTFOLIO_SCHEMA = "portfolio" if POSTGRESQL_STORAGE else None
ENTITY_ID_TYPE = Uuid(as_uuid=True) if POSTGRESQL_STORAGE else Integer
TENANT_ID_TYPE = Uuid(as_uuid=True) if POSTGRESQL_STORAGE else String(36)
TIMESTAMP_TYPE = DateTime(timezone=POSTGRESQL_STORAGE)


def _table_args(*constraints):
    if PORTFOLIO_SCHEMA:
        return (*constraints, {"schema": PORTFOLIO_SCHEMA})
    return constraints


def _table_name(name: str) -> str:
    if PORTFOLIO_SCHEMA:
        return f"{PORTFOLIO_SCHEMA}.{name}"
    return name


def _entity_id(*, singleton: bool = False):
    if singleton:
        return Column(
            Integer,
            primary_key=True,
            autoincrement=False,
            default=1,
        )
    if POSTGRESQL_STORAGE:
        return Column(
            ENTITY_ID_TYPE,
            primary_key=True,
            default=uuid7,
            autoincrement=False,
        )
    return Column(
        ENTITY_ID_TYPE,
        primary_key=True,
        autoincrement=not singleton,
        default=1 if singleton else None,
    )


def _tenant_id():
    default_value = (
        uuid.UUID(settings.PORTFOLIO_TENANT_ID)
        if POSTGRESQL_STORAGE
        else settings.PORTFOLIO_TENANT_ID
    )
    return Column(
        TENANT_ID_TYPE,
        nullable=False,
        default=default_value,
        server_default=settings.PORTFOLIO_TENANT_ID,
        index=True,
    )


def _utc_now() -> datetime.datetime:
    """UTC timestamp with microseconds so audit rows stay strictly ordered."""
    value = datetime.datetime.now(datetime.timezone.utc)
    return value if POSTGRESQL_STORAGE else value.replace(tzinfo=None)


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
    __table_args__ = _table_args()

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(String(120), nullable=False, default=current_user_key, index=True)
    ticker = Column(String(20), nullable=False, index=True)
    direction = Column(SAEnum(SignalDirection), nullable=False)
    entry_price = Column(PriceValue(), nullable=False)
    exit_price = Column(PriceValue(), nullable=True)
    quantity = Column(QuantityValue(), nullable=False)
    stop_loss = Column(PriceValue(), nullable=False)
    target_price = Column(PriceValue(), nullable=False)
    trailing_stop = Column(PriceValue(), nullable=True)
    asset_type = Column(String(20), nullable=False, default="stock")
    sector = Column(String(100), nullable=False, default="Unclassified")
    market_regime = Column(String(20), nullable=False, default="unknown")
    volatility_regime = Column(String(20), nullable=False, default="unknown")
    breadth_regime = Column(String(20), nullable=False, default="unknown")
    risk_regime = Column(String(20), nullable=False, default="unknown")
    regime_label = Column(String(200), nullable=False, default="Unknown")
    timeframe_agreement = Column(ScoreValue(), nullable=True)
    strategy_name = Column(String(100), nullable=True)
    strategy_version = Column(String(50), nullable=True)
    timeframe = Column(String(20), nullable=True)
    signal_confidence = Column(ScoreValue(), nullable=True)
    signal_context_json = Column(Text, nullable=True)
    execution_context_json = Column(Text, nullable=True)
    planned_entry_price = Column(PriceValue(), nullable=True)
    planned_exit_price = Column(PriceValue(), nullable=True)
    planned_quantity = Column(QuantityValue(), nullable=True)
    entry_fees = Column(MoneyValue(), nullable=False, default=0.0)
    entry_slippage = Column(MoneyValue(), nullable=False, default=0.0)
    entry_costs_allocated = Column(MoneyValue(), nullable=False, default=0.0)
    exit_fees_total = Column(MoneyValue(), nullable=False, default=0.0)
    exit_slippage_total = Column(MoneyValue(), nullable=False, default=0.0)
    costs_total = Column(MoneyValue(), nullable=False, default=0.0)
    realized_quantity = Column(QuantityValue(), nullable=False, default=0.0)
    gross_pnl = Column(MoneyValue(), nullable=True)
    mfe_usd = Column(MoneyValue(), nullable=True)
    mae_usd = Column(MoneyValue(), nullable=True)
    mfe_pct = Column(RatioValue(), nullable=True)
    mae_pct = Column(RatioValue(), nullable=True)
    excursion_status = Column(String(20), nullable=False, default="not_calculated")
    status = Column(SAEnum(TradeStatus), default=TradeStatus.OPEN)
    pnl = Column(MoneyValue(), nullable=True)
    pnl_pct = Column(RatioValue(), nullable=True)
    opened_at = Column(TIMESTAMP_TYPE, server_default=func.now())
    closed_at = Column(TIMESTAMP_TYPE, nullable=True)

    @property
    def remaining_quantity(self) -> float:
        return round(
            float(self.quantity or 0) - float(self.realized_quantity or 0),
            8,
        )


class TradeExecution(Base):
    """Individual entry/exit fill so fees and partial P&L stay auditable."""

    __tablename__ = "trade_executions"
    __table_args__ = _table_args()

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(String(120), nullable=False, default=current_user_key, index=True)
    trade_id = Column(
        ENTITY_ID_TYPE,
        ForeignKey(f"{_table_name('trades')}.id"),
        nullable=False,
        index=True,
    )
    kind = Column(SAEnum(ExecutionKind), nullable=False)
    price = Column(PriceValue(), nullable=False)
    quantity = Column(QuantityValue(), nullable=False)
    fees = Column(MoneyValue(), nullable=False, default=0.0)
    slippage = Column(MoneyValue(), nullable=False, default=0.0)
    entry_costs_allocated = Column(MoneyValue(), nullable=False, default=0.0)
    gross_pnl = Column(MoneyValue(), nullable=True)
    net_pnl = Column(MoneyValue(), nullable=True)
    note = Column(Text, nullable=True)
    executed_at = Column(TIMESTAMP_TYPE, server_default=func.now())


class TradeJournal(Base):
    """One automated post-trade journal entry per fully closed trade."""

    __tablename__ = "trade_journals"
    __table_args__ = _table_args()

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(String(120), nullable=False, default=current_user_key, index=True)
    trade_id = Column(
        ENTITY_ID_TYPE,
        ForeignKey(f"{_table_name('trades')}.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    ticker = Column(String(20), nullable=False, index=True)
    strategy_name = Column(String(100), nullable=True)
    outcome = Column(String(20), nullable=False)
    net_pnl = Column(MoneyValue(), nullable=False)
    summary = Column(Text, nullable=False)
    journal_json = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP_TYPE, server_default=func.now())


class PaperOrder(Base):
    """A simulated order. There is no live-money submission path."""

    __tablename__ = "paper_orders"
    __table_args__ = (
        _table_args(
            UniqueConstraint(
                "tenant_id",
                "user_key",
                "idempotency_key",
                name="uq_paper_order_idempotency",
            ),
        )
        if POSTGRESQL_STORAGE
        else ()
    )

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(String(120), nullable=False, default=current_user_key, index=True)
    idempotency_key = Column(
        String(120),
        nullable=False,
        unique=not POSTGRESQL_STORAGE,
        index=True,
    )
    ticker = Column(String(20), nullable=False, index=True)
    asset_type = Column(String(20), nullable=False, default="stock")
    side = Column(String(10), nullable=False)
    order_type = Column(String(20), nullable=False)
    role = Column(String(20), nullable=False, default="standalone")
    status = Column(String(20), nullable=False, default="pending", index=True)
    quantity = Column(QuantityValue(), nullable=False)
    filled_quantity = Column(QuantityValue(), nullable=False, default=0.0)
    limit_price = Column(PriceValue(), nullable=True)
    stop_price = Column(PriceValue(), nullable=True)
    trail_percent = Column(RatioValue(), nullable=True)
    trail_amount = Column(PriceValue(), nullable=True)
    trail_reference_price = Column(PriceValue(), nullable=True)
    effective_stop_price = Column(PriceValue(), nullable=True)
    triggered = Column(Boolean, nullable=False, default=False)
    triggered_at = Column(TIMESTAMP_TYPE, nullable=True)
    time_in_force = Column(String(10), nullable=False, default="gtc")
    expires_at = Column(TIMESTAMP_TYPE, nullable=True)
    reference_price = Column(PriceValue(), nullable=True)
    reserved_cash = Column(MoneyValue(), nullable=False, default=0.0)
    reservation_price = Column(PriceValue(), nullable=True)
    average_fill_price = Column(PriceValue(), nullable=True)
    filled_notional = Column(MoneyValue(), nullable=False, default=0.0)
    fees_total = Column(MoneyValue(), nullable=False, default=0.0)
    slippage_total = Column(MoneyValue(), nullable=False, default=0.0)
    parent_id = Column(
        ENTITY_ID_TYPE,
        ForeignKey(f"{_table_name('paper_orders')}.id"),
        nullable=True,
        index=True,
    )
    oco_group = Column(String(60), nullable=True, index=True)
    trade_id = Column(
        ENTITY_ID_TYPE,
        ForeignKey(f"{_table_name('trades')}.id"),
        nullable=True,
        index=True,
    )
    last_candle_at = Column(TIMESTAMP_TYPE, nullable=True)
    reject_reason = Column(Text, nullable=True)
    cancel_reason = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now)
    updated_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now, onupdate=_utc_now)

    @property
    def remaining_quantity(self) -> float:
        return round(
            float(self.quantity or 0) - float(self.filled_quantity or 0),
            8,
        )

    @property
    def costs_total(self) -> float:
        return round(
            float(self.fees_total or 0) + float(self.slippage_total or 0),
            6,
        )


class PaperOrderFill(Base):
    """One immutable simulated execution against a single candle."""

    __tablename__ = "paper_order_fills"
    __table_args__ = _table_args()

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(String(120), nullable=False, default=current_user_key, index=True)
    order_id = Column(
        ENTITY_ID_TYPE,
        ForeignKey(f"{_table_name('paper_orders')}.id"),
        nullable=False,
        index=True,
    )
    quantity = Column(QuantityValue(), nullable=False)
    price = Column(PriceValue(), nullable=False)
    fees = Column(MoneyValue(), nullable=False, default=0.0)
    slippage = Column(MoneyValue(), nullable=False, default=0.0)
    candle_timestamp = Column(TIMESTAMP_TYPE, nullable=False)
    trade_id = Column(
        ENTITY_ID_TYPE,
        ForeignKey(f"{_table_name('trades')}.id"),
        nullable=True,
        index=True,
    )
    created_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now)


class PaperOrderEvent(Base):
    """Append-only audit event; rows are never updated or deleted."""

    __tablename__ = "paper_order_events"
    __table_args__ = _table_args()

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(String(120), nullable=False, default=current_user_key, index=True)
    order_id = Column(
        ENTITY_ID_TYPE,
        ForeignKey(f"{_table_name('paper_orders')}.id"),
        nullable=False,
        index=True,
    )
    event_type = Column(String(40), nullable=False)
    from_status = Column(String(20), nullable=True)
    to_status = Column(String(20), nullable=True)
    message = Column(Text, nullable=True)
    detail_json = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now)


class Portfolio(Base):
    __tablename__ = "portfolios" if POSTGRESQL_STORAGE else "portfolio"
    __table_args__ = (
        _table_args(
            Index(
                "uq_portfolio_tenant_user",
                "tenant_id",
                "user_key",
                unique=True,
            ),
        )
        if POSTGRESQL_STORAGE
        else (Index("uq_portfolio_user_key", "user_key", unique=True),)
    )

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(String(120), nullable=False, default=current_user_key)
    balance = Column(MoneyValue(), nullable=False)
    equity = Column(MoneyValue(), nullable=False)
    total_pnl = Column(MoneyValue(), default=0.0)
    win_count = Column(Integer, default=0)
    loss_count = Column(Integer, default=0)
    max_drawdown = Column(RatioValue(), default=0.0)
    peak_equity = Column(MoneyValue(), nullable=False)
    updated_at = Column(TIMESTAMP_TYPE, server_default=func.now(), onupdate=func.now())


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    __table_args__ = _table_args()

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(String(120), nullable=False, default=current_user_key, index=True)
    equity = Column(MoneyValue(), nullable=False)
    balance = Column(MoneyValue(), nullable=False)
    timestamp = Column(TIMESTAMP_TYPE, server_default=func.now())


class RiskSnapshot(Base):
    __tablename__ = "risk_snapshots"
    __table_args__ = _table_args()

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(
        String(120),
        nullable=False,
        default=current_user_key,
        index=True,
    )
    equity = Column(MoneyValue(), nullable=False)
    snapshot_json = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now)


class AlertLog(Base):
    __tablename__ = "alert_logs"
    __table_args__ = _table_args()

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(String(120), nullable=False, default=current_user_key, index=True)
    ticker = Column(String(20), nullable=False, index=True)
    direction = Column(String(20), nullable=False)
    status = Column(String(50), nullable=False)
    trigger_price = Column(PriceValue(), nullable=False)
    stop_loss = Column(PriceValue(), nullable=True)
    target_price = Column(PriceValue(), nullable=True)
    optimal_size_usd = Column(MoneyValue(), nullable=True)
    kelly_pct = Column(RatioValue(), nullable=True)
    capital_overspend = Column(Boolean, default=False)
    approved = Column(Boolean, default=True)
    message = Column(Text, nullable=True)
    risk_decision_json = Column(Text, nullable=True)
    market_regime = Column(String(20), nullable=False, default="unknown")
    volatility_regime = Column(String(20), nullable=False, default="unknown")
    breadth_regime = Column(String(20), nullable=False, default="unknown")
    risk_regime = Column(String(20), nullable=False, default="unknown")
    regime_label = Column(String(200), nullable=False, default="Unknown")
    timeframe_agreement = Column(ScoreValue(), nullable=True)
    created_at = Column(TIMESTAMP_TYPE, server_default=func.now())


class CredentialSecret(Base):
    __tablename__ = "credential_secrets"
    __table_args__ = _table_args()

    id = _entity_id()
    provider = Column(String(50), nullable=False, index=True)
    key = Column(String(100), nullable=False, unique=True, index=True)
    value = Column(Text, nullable=False)
    verified = Column(Boolean, default=False)
    last_error = Column(Text, nullable=True)
    updated_at = Column(TIMESTAMP_TYPE, server_default=func.now(), onupdate=func.now())


class ActionItem(Base):
    """A durable Action Required inbox row deduplicated by (user_key, source_key)."""

    __tablename__ = "action_items"
    __table_args__ = (
        _table_args(
            UniqueConstraint(
                "tenant_id",
                "user_key",
                "source_key",
                name="uq_action_item_source",
            ),
        )
        if POSTGRESQL_STORAGE
        else (
            UniqueConstraint(
                "user_key",
                "source_key",
                name="uq_action_item_source",
            ),
        )
    )

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(String(120), nullable=False, default="default", index=True)
    source_key = Column(String(200), nullable=False, index=True)
    source_type = Column(String(40), nullable=False)
    category = Column(String(40), nullable=False, index=True)
    severity = Column(String(20), nullable=False, index=True)
    is_mandatory = Column(Boolean, nullable=False, default=False)
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    ticker = Column(String(20), nullable=True, index=True)
    trade_id = Column(ENTITY_ID_TYPE, nullable=True)
    order_id = Column(ENTITY_ID_TYPE, nullable=True)
    context_id = Column(String(120), nullable=True)
    deep_link_tab = Column(String(40), nullable=False)
    deep_link_json = Column(Text, nullable=False, default="{}")
    payload_json = Column(Text, nullable=False, default="{}")
    payload_hash = Column(String(64), nullable=False, default="")
    status = Column(String(20), nullable=False, default="open", index=True)
    source_active = Column(Boolean, nullable=False, default=True)
    snoozed_until = Column(TIMESTAMP_TYPE, nullable=True)
    first_seen_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now)
    last_seen_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now)
    updated_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now, onupdate=_utc_now)
    acknowledged_at = Column(TIMESTAMP_TYPE, nullable=True)
    snoozed_at = Column(TIMESTAMP_TYPE, nullable=True)
    resolved_at = Column(TIMESTAMP_TYPE, nullable=True)


class DashboardPreference(Base):
    """Per-user dashboard widget layout, density mode, and named saved layouts."""

    __tablename__ = "dashboard_preferences"
    __table_args__ = (
        _table_args(
            UniqueConstraint(
                "tenant_id",
                "user_key",
                name="uq_dashboard_preference_tenant_user",
            ),
        )
        if POSTGRESQL_STORAGE
        else ()
    )

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(
        String(120),
        nullable=False,
        unique=not POSTGRESQL_STORAGE,
        index=True,
    )
    widgets_json = Column(Text, nullable=False, default="[]")
    mode = Column(String(20), nullable=False, default="detailed")
    layouts_json = Column(Text, nullable=False, default="{}")
    updated_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now, onupdate=_utc_now)


class LiveTradingControl(Base):
    """Singleton row holding the live-execution arming state and kill switch.

    Live trading needs both configuration (``LIVE_TRADING_ENABLED``) and a
    durable operator acknowledgement recorded here, so restarting the service
    with the flag on is not by itself enough to submit an order.
    """

    __tablename__ = "live_trading_control"
    __table_args__ = _table_args(
        CheckConstraint("id = 1", name="ck_live_trading_control_singleton"),
    )

    id = _entity_id(singleton=True)
    acknowledged = Column(Boolean, nullable=False, default=False)
    acknowledged_by = Column(String(120), nullable=True)
    acknowledged_at = Column(TIMESTAMP_TYPE, nullable=True)
    acknowledgement_note = Column(Text, nullable=True)
    trading_disabled = Column(Boolean, nullable=False, default=False)
    disabled_reason = Column(Text, nullable=True)
    disabled_by = Column(String(120), nullable=True)
    disabled_at = Column(TIMESTAMP_TYPE, nullable=True)
    updated_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now, onupdate=_utc_now)


class LiveOrder(Base):
    """A real-money order routed to a broker.

    Stored separately from ``paper_orders`` so simulated and live executions can
    never be listed, reconciled or aggregated together by accident.
    """

    __tablename__ = "live_orders"
    __table_args__ = (
        _table_args(
            UniqueConstraint(
                "tenant_id",
                "user_key",
                "idempotency_key",
                name="uq_live_order_idempotency",
            ),
            UniqueConstraint(
                "tenant_id",
                "client_order_id",
                name="uq_live_order_client_id",
            ),
        )
        if POSTGRESQL_STORAGE
        else ()
    )

    id = _entity_id()
    tenant_id = _tenant_id()
    idempotency_key = Column(
        String(120),
        nullable=False,
        unique=not POSTGRESQL_STORAGE,
        index=True,
    )
    client_order_id = Column(
        String(120),
        nullable=False,
        unique=not POSTGRESQL_STORAGE,
        index=True,
    )
    broker = Column(String(40), nullable=False)
    broker_order_id = Column(String(120), nullable=True, index=True)
    broker_endpoint = Column(String(200), nullable=False, default="")
    sandbox = Column(Boolean, nullable=False, default=True)
    user_key = Column(String(120), nullable=False, default="default", index=True)
    ticker = Column(String(20), nullable=False, index=True)
    asset_type = Column(String(20), nullable=False, default="stock")
    side = Column(String(10), nullable=False)
    order_type = Column(String(20), nullable=False)
    quantity = Column(QuantityValue(), nullable=False)
    filled_quantity = Column(QuantityValue(), nullable=False, default=0.0)
    limit_price = Column(PriceValue(), nullable=True)
    stop_price = Column(PriceValue(), nullable=True)
    time_in_force = Column(String(10), nullable=False, default="day")
    reference_price = Column(PriceValue(), nullable=True)
    estimated_notional = Column(MoneyValue(), nullable=True)
    average_fill_price = Column(PriceValue(), nullable=True)
    status = Column(String(20), nullable=False, default="new", index=True)
    preflight_json = Column(Text, nullable=False, default="[]")
    request_fingerprint = Column(String(64), nullable=False, default="")
    reject_reason = Column(Text, nullable=True)
    cancel_reason = Column(Text, nullable=True)
    submitted_at = Column(TIMESTAMP_TYPE, nullable=True)
    reconciled_at = Column(TIMESTAMP_TYPE, nullable=True)
    broker_status_raw = Column(String(40), nullable=True)
    created_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now)
    updated_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now, onupdate=_utc_now)


class LiveOrderFill(Base):
    """A fill reported by the broker, deduplicated by broker fill identity."""

    __tablename__ = "live_order_fills"
    __table_args__ = (
        *_table_args(
            UniqueConstraint(
                "order_id",
                "broker_fill_id",
                name="uq_live_fill_broker_id",
            ),
        ),
    )

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(String(120), nullable=False, default=current_user_key, index=True)
    order_id = Column(
        ENTITY_ID_TYPE,
        ForeignKey(f"{_table_name('live_orders')}.id"),
        nullable=False,
        index=True,
    )
    broker_fill_id = Column(String(120), nullable=False)
    quantity = Column(QuantityValue(), nullable=False)
    price = Column(PriceValue(), nullable=False)
    filled_at = Column(TIMESTAMP_TYPE, nullable=True)
    created_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now)


class LiveExecutionAudit(Base):
    """Append-only, hash-chained audit of every live-execution action.

    ``entry_hash`` commits to ``previous_hash`` and the record body, so any
    later edit or deletion is detectable through ``/api/live-orders/audit/verify``.
    """

    __tablename__ = "live_execution_audit"
    __table_args__ = _table_args()

    id = _entity_id()
    tenant_id = _tenant_id()
    user_key = Column(String(120), nullable=False, default=current_user_key, index=True)
    order_id = Column(
        ENTITY_ID_TYPE,
        ForeignKey(f"{_table_name('live_orders')}.id"),
        nullable=True,
        index=True,
    )
    event_type = Column(String(40), nullable=False, index=True)
    actor = Column(String(120), nullable=False, default="default")
    message = Column(Text, nullable=False, default="")
    record_json = Column(Text, nullable=False, default="{}")
    previous_hash = Column(String(64), nullable=False, default="")
    entry_hash = Column(String(64), nullable=False, default="")
    created_at = Column(TIMESTAMP_TYPE, nullable=False, default=_utc_now)


class User(Base):
    __tablename__ = "users"
    __table_args__ = _table_args()

    id = _entity_id()
    username = Column(String(100), nullable=False, unique=True, index=True)
    password_hash = Column(String(256), nullable=False)
    display_name = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP_TYPE, server_default=func.now())


USER_SCOPED_MODELS = (
    Trade,
    TradeExecution,
    TradeJournal,
    PaperOrder,
    PaperOrderFill,
    PaperOrderEvent,
    Portfolio,
    EquitySnapshot,
    RiskSnapshot,
    AlertLog,
    LiveOrderFill,
    LiveExecutionAudit,
)


@event.listens_for(Session, "do_orm_execute")
def _scope_user_owned_queries(execute_state) -> None:
    if not execute_state.is_select:
        return
    user_key = current_user_key() or DEFAULT_USER_KEY
    tenant_value = (
        uuid.UUID(settings.PORTFOLIO_TENANT_ID)
        if POSTGRESQL_STORAGE
        else settings.PORTFOLIO_TENANT_ID
    )
    statement = execute_state.statement
    for model in USER_SCOPED_MODELS:
        statement = statement.options(
            with_loader_criteria(
                model,
                (model.tenant_id == tenant_value) & (model.user_key == user_key),
                include_aliases=True,
            )
        )
    execute_state.statement = statement
