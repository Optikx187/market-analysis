from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text


def expected_revision() -> str:
    service_root = Path(__file__).resolve().parents[1]
    config = Config(str(service_root / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_current_head()
    if revision is None:
        raise RuntimeError("No backtest migration head is configured")
    return revision


def _database_url(database_url_or_path: str) -> str:
    if "://" in database_url_or_path:
        if database_url_or_path.startswith("postgresql://"):
            return database_url_or_path.replace(
                "postgresql://",
                "postgresql+psycopg://",
                1,
            )
        return database_url_or_path
    return f"sqlite:///{Path(database_url_or_path).resolve()}"


def current_revision(database_url_or_path: str) -> str | None:
    database_url = _database_url(database_url_or_path)
    if database_url.startswith("sqlite:///"):
        path = Path(database_url.removeprefix("sqlite:///"))
        if not path.is_file():
            return None
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            version_table = (
                "alembic_version_research"
                if connection.dialect.name == "postgresql"
                else "alembic_version"
            )
            if version_table not in inspect(connection).get_table_names():
                return None
            revision = connection.execute(
                text(f"SELECT version_num FROM {version_table}")
            ).scalar_one_or_none()
        return str(revision) if revision is not None else None
    finally:
        engine.dispose()


def validate_schema(database_url_or_path: str) -> None:
    expected = expected_revision()
    current = current_revision(database_url_or_path)
    if current != expected:
        current_label = current or "unversioned"
        raise RuntimeError(
            "Backtest database migration required "
            f"(current={current_label}, expected={expected}). "
            "Create a verified backup, then run './manage migrate'."
        )
