from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


def _migrate_existing_tables(conn: Connection) -> None:
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    if "trades" in tables:
        columns = {column["name"] for column in inspector.get_columns("trades")}
        if "asset_type" not in columns:
            conn.execute(text("ALTER TABLE trades ADD COLUMN asset_type VARCHAR(20) NOT NULL DEFAULT 'stock'"))
        if "sector" not in columns:
            conn.execute(text("ALTER TABLE trades ADD COLUMN sector VARCHAR(100) NOT NULL DEFAULT 'Unclassified'"))
        regime_columns = {
            "market_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "volatility_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "breadth_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "risk_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "regime_label": "VARCHAR(200) NOT NULL DEFAULT 'Unknown'",
            "timeframe_agreement": "FLOAT",
        }
        for name, definition in regime_columns.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE trades ADD COLUMN {name} {definition}"))
        attribution_columns = {
            "strategy_name": "VARCHAR(100)",
            "strategy_version": "VARCHAR(50)",
            "timeframe": "VARCHAR(20)",
            "signal_confidence": "FLOAT",
            "signal_context_json": "TEXT",
            "execution_context_json": "TEXT",
            "planned_entry_price": "FLOAT",
            "planned_exit_price": "FLOAT",
            "planned_quantity": "FLOAT",
            "entry_fees": "FLOAT NOT NULL DEFAULT 0",
            "entry_slippage": "FLOAT NOT NULL DEFAULT 0",
            "entry_costs_allocated": "FLOAT NOT NULL DEFAULT 0",
            "exit_fees_total": "FLOAT NOT NULL DEFAULT 0",
            "exit_slippage_total": "FLOAT NOT NULL DEFAULT 0",
            "costs_total": "FLOAT NOT NULL DEFAULT 0",
            "realized_quantity": "FLOAT NOT NULL DEFAULT 0",
            "gross_pnl": "FLOAT",
            "mfe_usd": "FLOAT",
            "mae_usd": "FLOAT",
            "mfe_pct": "FLOAT",
            "mae_pct": "FLOAT",
            "excursion_status": "VARCHAR(20) NOT NULL DEFAULT 'not_calculated'",
        }
        for name, definition in attribution_columns.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE trades ADD COLUMN {name} {definition}"))
        if {"quantity", "status", "pnl"}.issubset(columns):
            conn.execute(text(
                "UPDATE trades SET realized_quantity = quantity "
                "WHERE status = 'CLOSED' AND realized_quantity = 0"
            ))
            conn.execute(text(
                "UPDATE trades SET gross_pnl = pnl WHERE gross_pnl IS NULL AND pnl IS NOT NULL"
            ))
    if "paper_orders" in tables:
        columns = {column["name"] for column in inspector.get_columns("paper_orders")}
        paper_order_columns = {
            "role": "VARCHAR(20) NOT NULL DEFAULT 'standalone'",
            "asset_type": "VARCHAR(20) NOT NULL DEFAULT 'stock'",
            "filled_quantity": "FLOAT NOT NULL DEFAULT 0",
            "limit_price": "FLOAT",
            "stop_price": "FLOAT",
            "trail_percent": "FLOAT",
            "trail_amount": "FLOAT",
            "trail_reference_price": "FLOAT",
            "effective_stop_price": "FLOAT",
            "triggered": "BOOLEAN NOT NULL DEFAULT 0",
            "triggered_at": "DATETIME",
            "time_in_force": "VARCHAR(10) NOT NULL DEFAULT 'gtc'",
            "expires_at": "DATETIME",
            "reference_price": "FLOAT",
            "reserved_cash": "FLOAT NOT NULL DEFAULT 0",
            "reservation_price": "FLOAT",
            "average_fill_price": "FLOAT",
            "filled_notional": "FLOAT NOT NULL DEFAULT 0",
            "fees_total": "FLOAT NOT NULL DEFAULT 0",
            "slippage_total": "FLOAT NOT NULL DEFAULT 0",
            "parent_id": "INTEGER",
            "oco_group": "VARCHAR(60)",
            "trade_id": "INTEGER",
            "last_candle_at": "DATETIME",
            "reject_reason": "TEXT",
            "cancel_reason": "TEXT",
            "created_at": "DATETIME",
            "updated_at": "DATETIME",
        }
        for name, definition in paper_order_columns.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE paper_orders ADD COLUMN {name} {definition}"))
    if "action_items" in tables:
        columns = {column["name"] for column in inspector.get_columns("action_items")}
        action_item_columns = {
            "user_key": "VARCHAR(120) NOT NULL DEFAULT 'default'",
            "source_type": "VARCHAR(40) NOT NULL DEFAULT 'operational'",
            "category": "VARCHAR(40) NOT NULL DEFAULT 'operations'",
            "severity": "VARCHAR(20) NOT NULL DEFAULT 'info'",
            "is_mandatory": "BOOLEAN NOT NULL DEFAULT 0",
            "title": "VARCHAR(200) NOT NULL DEFAULT ''",
            "message": "TEXT NOT NULL DEFAULT ''",
            "ticker": "VARCHAR(20)",
            "trade_id": "INTEGER",
            "order_id": "INTEGER",
            "context_id": "VARCHAR(120)",
            "deep_link_tab": "VARCHAR(40) NOT NULL DEFAULT 'settings'",
            "deep_link_json": "TEXT NOT NULL DEFAULT '{}'",
            "payload_json": "TEXT NOT NULL DEFAULT '{}'",
            "payload_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
            "status": "VARCHAR(20) NOT NULL DEFAULT 'open'",
            "source_active": "BOOLEAN NOT NULL DEFAULT 1",
            "snoozed_until": "DATETIME",
            "first_seen_at": "DATETIME",
            "last_seen_at": "DATETIME",
            "updated_at": "DATETIME",
            "acknowledged_at": "DATETIME",
            "snoozed_at": "DATETIME",
            "resolved_at": "DATETIME",
        }
        for name, definition in action_item_columns.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE action_items ADD COLUMN {name} {definition}"))
        index_names = {index["name"] for index in inspector.get_indexes("action_items")}
        if "uq_action_item_source" not in index_names:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_action_item_source "
                "ON action_items (user_key, source_key)"
            ))
    if "dashboard_preferences" in tables:
        columns = {column["name"] for column in inspector.get_columns("dashboard_preferences")}
        preference_columns = {
            "widgets_json": "TEXT NOT NULL DEFAULT '[]'",
            "mode": "VARCHAR(20) NOT NULL DEFAULT 'detailed'",
            "layouts_json": "TEXT NOT NULL DEFAULT '{}'",
            "updated_at": "DATETIME",
        }
        for name, definition in preference_columns.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE dashboard_preferences ADD COLUMN {name} {definition}"))
    if "alert_logs" in tables:
        columns = {column["name"] for column in inspector.get_columns("alert_logs")}
        if "approved" not in columns:
            conn.execute(text("ALTER TABLE alert_logs ADD COLUMN approved BOOLEAN NOT NULL DEFAULT 1"))
        if "risk_decision_json" not in columns:
            conn.execute(text("ALTER TABLE alert_logs ADD COLUMN risk_decision_json TEXT"))
        regime_columns = {
            "market_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "volatility_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "breadth_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "risk_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "regime_label": "VARCHAR(200) NOT NULL DEFAULT 'Unknown'",
            "timeframe_agreement": "FLOAT",
        }
        for name, definition in regime_columns.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE alert_logs ADD COLUMN {name} {definition}"))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_existing_tables)
