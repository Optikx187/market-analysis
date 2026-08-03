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
    if "alert_logs" in tables:
        columns = {column["name"] for column in inspector.get_columns("alert_logs")}
        if "approved" not in columns:
            conn.execute(text("ALTER TABLE alert_logs ADD COLUMN approved BOOLEAN NOT NULL DEFAULT 1"))
        if "risk_decision_json" not in columns:
            conn.execute(text("ALTER TABLE alert_logs ADD COLUMN risk_decision_json TEXT"))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_existing_tables)
