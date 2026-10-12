import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def expected_revision() -> str:
    service_root = Path(__file__).resolve().parents[1]
    config = Config(str(service_root / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_current_head()
    if revision is None:
        raise RuntimeError("No backtest migration head is configured")
    return revision


def current_revision(database_path: str) -> str | None:
    path = Path(database_path)
    if not path.is_file():
        return None
    with sqlite3.connect(path) as connection:
        version_table = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone()
        if version_table is None:
            return None
        row = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    return str(row[0]) if row is not None else None


def validate_schema(database_path: str) -> None:
    expected = expected_revision()
    current = current_revision(database_path)
    if current != expected:
        current_label = current or "unversioned"
        raise RuntimeError(
            "Backtest database migration required "
            f"(current={current_label}, expected={expected}). "
            "Create a verified backup, then run './manage migrate'."
        )
