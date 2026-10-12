from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def migrate_database(database_path: Path) -> None:
    original_path = settings.BACKTEST_DATABASE_PATH
    settings.BACKTEST_DATABASE_PATH = str(database_path)
    try:
        command.upgrade(
            Config(str(SERVICE_ROOT / "alembic.ini")),
            "head",
        )
    finally:
        settings.BACKTEST_DATABASE_PATH = original_path
