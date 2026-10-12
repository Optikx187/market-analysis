from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import engine


def expected_revision() -> str:
    service_root = Path(__file__).resolve().parents[1]
    config = Config(str(service_root / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_current_head()
    if revision is None:
        raise RuntimeError("No portfolio migration head is configured")
    return revision


def _current_revision(connection: Connection) -> str | None:
    if "alembic_version" not in inspect(connection).get_table_names():
        return None
    return connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one_or_none()


async def validate_schema(database_engine: AsyncEngine | None = None) -> None:
    expected = expected_revision()
    target_engine = database_engine or engine
    async with target_engine.connect() as connection:
        current = await connection.run_sync(_current_revision)
    if current != expected:
        current_label = current or "unversioned"
        raise RuntimeError(
            "Portfolio database migration required "
            f"(current={current_label}, expected={expected}). "
            "Create a verified backup, then run './manage migrate'."
        )
