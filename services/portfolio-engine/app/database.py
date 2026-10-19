from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


if engine.dialect.name == "postgresql":
    @event.listens_for(engine.sync_engine, "connect")
    def _set_postgresql_utc(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("SET TIME ZONE 'UTC'")
        cursor.close()


async def acquire_idempotency_lock(
    db: AsyncSession,
    operation: str,
    actor: str,
    key: str,
) -> None:
    if engine.dialect.name != "postgresql":
        return
    await db.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:lock_key, 0)"
            ")"
        ),
        {"lock_key": f"{operation}:{actor}:{key}"},
    )


async def get_db():
    async with async_session() as session:
        yield session
