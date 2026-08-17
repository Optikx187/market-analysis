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
