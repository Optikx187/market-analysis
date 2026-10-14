import asyncio
import hashlib
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.migration_state import validate_schema


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _run(database_path: Path, revision: str, *, downgrade: bool = False) -> None:
    original_url = settings.DATABASE_URL
    settings.DATABASE_URL = f"sqlite+aiosqlite:///{database_path}"
    try:
        config = Config(str(SERVICE_ROOT / "alembic.ini"))
        if downgrade:
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)
    finally:
        settings.DATABASE_URL = original_url


def _check(database_path: Path) -> None:
    original_url = settings.DATABASE_URL
    settings.DATABASE_URL = f"sqlite+aiosqlite:///{database_path}"
    try:
        command.check(Config(str(SERVICE_ROOT / "alembic.ini")))
    finally:
        settings.DATABASE_URL = original_url


def _digest(database_path: Path) -> str:
    with sqlite3.connect(database_path) as connection:
        schema = connection.execute(
            "SELECT type, name, COALESCE(sql, '') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        assets = connection.execute(
            "SELECT * FROM assets ORDER BY id"
        ).fetchall() if any(row[1] == "assets" for row in schema) else []
        candles = connection.execute(
            "SELECT * FROM candles ORDER BY id"
        ).fetchall() if any(row[1] == "candles" for row in schema) else []
    return hashlib.sha256(
        repr((schema, assets, candles)).encode()
    ).hexdigest()


def _create_existing_fixture(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE assets (
                id INTEGER NOT NULL PRIMARY KEY,
                ticker VARCHAR(20) NOT NULL,
                name VARCHAR(100) NOT NULL,
                asset_type VARCHAR(6) NOT NULL,
                is_active BOOLEAN,
                created_at DATETIME
            );
            CREATE UNIQUE INDEX ix_assets_ticker ON assets (ticker);
            CREATE TABLE candles (
                id INTEGER NOT NULL PRIMARY KEY,
                ticker VARCHAR(20) NOT NULL,
                timestamp DATETIME NOT NULL,
                open FLOAT NOT NULL,
                high FLOAT NOT NULL,
                low FLOAT NOT NULL,
                close FLOAT NOT NULL,
                volume FLOAT NOT NULL,
                interval VARCHAR(10)
            );
            CREATE INDEX ix_candles_ticker ON candles (ticker);
            CREATE INDEX ix_candles_timestamp ON candles (timestamp);
            CREATE TABLE price_alerts (
                id INTEGER NOT NULL PRIMARY KEY,
                ticker VARCHAR(20) NOT NULL,
                condition VARCHAR(10) NOT NULL,
                threshold FLOAT NOT NULL,
                triggered BOOLEAN,
                created_at DATETIME,
                triggered_at DATETIME
            );
            CREATE INDEX ix_price_alerts_ticker ON price_alerts (ticker);
            """
        )
        connection.execute(
            "INSERT INTO assets VALUES "
            "(1, 'BTC', 'Bitcoin', 'CRYPTO', 1, '2025-01-01 00:00:00')"
        )
        connection.execute(
            "INSERT INTO candles VALUES "
            "(1, 'BTC', '2025-01-01 00:00:00', "
            "100, 110, 90, 105, 1000, '1d')"
        )
        connection.execute(
            "INSERT INTO price_alerts VALUES "
            "(1, 'BTC', 'above', 120, 0, "
            "'2025-01-01 00:00:00', NULL)"
        )


def test_empty_database_upgrades_to_head_and_repeats_cleanly(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "empty.db"

    _run(database_path, "head")
    first_digest = _digest(database_path)
    _run(database_path, "head")

    assert _digest(database_path) == first_digest
    _check(database_path)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0002_data",)


def test_existing_database_is_adopted_without_losing_market_data(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "existing.db"
    _create_existing_fixture(database_path)

    _run(database_path, "head")
    _check(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT ticker, name, asset_type, is_active FROM assets"
        ).fetchall() == [("BTC", "Bitcoin", "CRYPTO", 1)]
        assert connection.execute(
            "SELECT ticker, open, high, low, close, volume, interval "
            "FROM candles"
        ).fetchall() == [
            ("BTC", 100.0, 110.0, 90.0, 105.0, 1000.0, "1d")
        ]
        assert connection.execute(
            "SELECT ticker, condition, threshold, triggered "
            "FROM price_alerts"
        ).fetchall() == [("BTC", "above", 120.0, 0)]
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0002_data",)


def test_destructive_downgrade_requires_verified_restore(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "rollback.db"
    _run(database_path, "head")

    with pytest.raises(RuntimeError, match="verified pre-migration backup"):
        _run(database_path, "base", downgrade=True)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0002_data",)


def test_schema_validation_is_read_only_and_fails_closed(
    tmp_path: Path,
) -> None:
    migrated_path = tmp_path / "migrated.db"
    _run(migrated_path, "head")
    migrated_digest = _digest(migrated_path)
    migrated_engine = create_async_engine(
        f"sqlite+aiosqlite:///{migrated_path}"
    )
    asyncio.run(validate_schema(migrated_engine))
    asyncio.run(validate_schema(migrated_engine))
    asyncio.run(migrated_engine.dispose())
    assert _digest(migrated_path) == migrated_digest

    unversioned_path = tmp_path / "unversioned.db"
    with sqlite3.connect(unversioned_path) as connection:
        connection.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO sentinel VALUES (1)")
    unversioned_digest = _digest(unversioned_path)
    unversioned_engine = create_async_engine(
        f"sqlite+aiosqlite:///{unversioned_path}"
    )
    with pytest.raises(RuntimeError, match="current=unversioned"):
        asyncio.run(validate_schema(unversioned_engine))
    asyncio.run(unversioned_engine.dispose())
    assert _digest(unversioned_path) == unversioned_digest


def test_stale_revision_fails_closed_without_mutation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "stale.db"
    _run(database_path, "head")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE alembic_version SET version_num='stale-revision'"
        )
    before = _digest(database_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")

    with pytest.raises(RuntimeError, match="current=stale-revision"):
        asyncio.run(validate_schema(engine))
    asyncio.run(engine.dispose())

    assert _digest(database_path) == before
