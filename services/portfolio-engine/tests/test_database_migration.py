import asyncio
import hashlib
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401
from app.config import settings
from app.database import Base
from app.migration_state import validate_schema


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    return Config(str(SERVICE_ROOT / "alembic.ini"))


def _upgrade(database_path: Path) -> None:
    original_url = settings.DATABASE_URL
    settings.DATABASE_URL = f"sqlite+aiosqlite:///{database_path}"
    try:
        command.upgrade(_config(), "head")
    finally:
        settings.DATABASE_URL = original_url


def _check(database_path: Path) -> None:
    original_url = settings.DATABASE_URL
    settings.DATABASE_URL = f"sqlite+aiosqlite:///{database_path}"
    try:
        command.check(_config())
    finally:
        settings.DATABASE_URL = original_url


def _downgrade(database_path: Path) -> None:
    original_url = settings.DATABASE_URL
    settings.DATABASE_URL = f"sqlite+aiosqlite:///{database_path}"
    try:
        command.downgrade(_config(), "base")
    finally:
        settings.DATABASE_URL = original_url


def _schema_digest(database_path: Path) -> str:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT type, name, COALESCE(sql, '') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def _create_legacy_fixture(database_path: Path) -> None:
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE trades ("
            "id INTEGER PRIMARY KEY, ticker VARCHAR(20) NOT NULL, "
            "quantity FLOAT, status VARCHAR(10), pnl FLOAT)"
        ))
        connection.execute(text(
            "INSERT INTO trades (ticker, quantity, status, pnl) VALUES "
            "('AAPL', 10, 'CLOSED', 120), ('BTC', 5, 'OPEN', NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE alert_logs ("
            "id INTEGER PRIMARY KEY, ticker VARCHAR(20) NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO alert_logs (ticker) VALUES ('AAPL')"
        ))
        connection.execute(text(
            "CREATE TABLE action_items ("
            "id INTEGER PRIMARY KEY, source_key VARCHAR(200) NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO action_items (source_key) VALUES ('earnings:AAPL')"
        ))
        connection.execute(text(
            "CREATE TABLE dashboard_preferences ("
            "id INTEGER PRIMARY KEY, user_key VARCHAR(120))"
        ))
        connection.execute(text(
            "INSERT INTO dashboard_preferences (user_key) VALUES ('default')"
        ))
        connection.execute(text(
            "CREATE TABLE paper_orders ("
            "id INTEGER PRIMARY KEY, idempotency_key VARCHAR(80), "
            "ticker VARCHAR(20) NOT NULL, side VARCHAR(10), "
            "order_type VARCHAR(20), status VARCHAR(20), quantity FLOAT)"
        ))
        connection.execute(text(
            "INSERT INTO paper_orders "
            "(idempotency_key, ticker, side, order_type, status, quantity) "
            "VALUES ('legacy-1', 'AAPL', 'BUY', 'market', 'submitted', 5)"
        ))
        connection.execute(text(
            "CREATE TABLE portfolio ("
            "id INTEGER PRIMARY KEY, balance FLOAT NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO portfolio (balance) VALUES (10000), (20000)"
        ))
        connection.execute(text(
            "CREATE TABLE trade_executions ("
            "id INTEGER PRIMARY KEY, trade_id INTEGER NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO trade_executions (trade_id) VALUES (1)"
        ))
    engine.dispose()


def test_empty_database_upgrades_to_head_and_is_idempotent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "empty.db"

    _upgrade(database_path)
    first_digest = _schema_digest(database_path)
    _upgrade(database_path)

    assert _schema_digest(database_path) == first_digest
    _check(database_path)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0001_portfolio",)


def test_current_unversioned_schema_is_adopted_without_data_loss(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "current-like.db"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO portfolio "
            "(user_key, balance, equity, peak_equity) "
            "VALUES ('alice', 12345, 12500, 13000)"
        ))
    engine.dispose()

    _upgrade(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT user_key, balance, equity, peak_equity FROM portfolio"
        ).fetchall() == [("alice", 12345.0, 12500.0, 13000.0)]
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0001_portfolio",)


def test_legacy_schema_upgrades_with_record_reconciliation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.db"
    _create_legacy_fixture(database_path)

    _upgrade(database_path)

    engine = create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        inspector = inspect(connection)
        trade_columns = {
            column["name"] for column in inspector.get_columns("trades")
        }
        trade_rows = connection.execute(text(
            "SELECT ticker, user_key, asset_type, sector, "
            "realized_quantity, gross_pnl, costs_total, excursion_status "
            "FROM trades ORDER BY id"
        )).all()
        alert_row = connection.execute(text(
            "SELECT user_key, approved, risk_decision_json, "
            "market_regime, regime_label FROM alert_logs"
        )).one()
        action_row = connection.execute(text(
            "SELECT user_key, status, severity, category, "
            "is_mandatory, source_active FROM action_items"
        )).one()
        preference_row = connection.execute(text(
            "SELECT user_key, widgets_json, mode, layouts_json "
            "FROM dashboard_preferences"
        )).one()
        paper_row = connection.execute(text(
            "SELECT ticker, user_key, asset_type, role, filled_quantity, "
            "triggered, time_in_force, reserved_cash, filled_notional "
            "FROM paper_orders"
        )).one()
        portfolio_rows = connection.execute(text(
            "SELECT balance, user_key FROM portfolio ORDER BY id"
        )).all()
        execution_row = connection.execute(text(
            "SELECT trade_id, user_key FROM trade_executions"
        )).one()
        action_indexes = {
            index["name"]
            for index in inspector.get_indexes("action_items")
        }
        execution_indexes = {
            index["name"]
            for index in inspector.get_indexes("trade_executions")
        }
    engine.dispose()

    assert {
        "market_regime",
        "volatility_regime",
        "breadth_regime",
        "risk_regime",
        "regime_label",
        "timeframe_agreement",
        "strategy_name",
        "gross_pnl",
        "realized_quantity",
        "excursion_status",
    }.issubset(trade_columns)
    assert trade_rows == [
        (
            "AAPL",
            "default",
            "stock",
            "Unclassified",
            10,
            120,
            0,
            "not_calculated",
        ),
        (
            "BTC",
            "default",
            "stock",
            "Unclassified",
            0,
            None,
            0,
            "not_calculated",
        ),
    ]
    assert alert_row == (
        "default",
        1,
        None,
        "unknown",
        "Unknown",
    )
    assert action_row == (
        "default",
        "open",
        "info",
        "operations",
        0,
        1,
    )
    assert preference_row == ("default", "[]", "detailed", "{}")
    assert paper_row == (
        "AAPL",
        "default",
        "stock",
        "standalone",
        0,
        0,
        "gtc",
        0,
        0,
    )
    assert portfolio_rows == [(10000, "default")]
    assert execution_row == (1, "default")
    assert "uq_action_item_source" in action_indexes
    assert "ix_trade_executions_user_key" in execution_indexes


def test_destructive_downgrade_requires_restore(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "rollback.db"
    _upgrade(database_path)

    with pytest.raises(RuntimeError, match="verified pre-migration backup"):
        _downgrade(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0001_portfolio",)


def test_schema_validation_is_read_only_and_fails_closed(
    tmp_path: Path,
) -> None:
    migrated_path = tmp_path / "migrated.db"
    _upgrade(migrated_path)
    migrated_digest = _schema_digest(migrated_path)
    migrated_engine = create_async_engine(
        f"sqlite+aiosqlite:///{migrated_path}"
    )
    asyncio.run(validate_schema(migrated_engine))
    asyncio.run(validate_schema(migrated_engine))
    asyncio.run(migrated_engine.dispose())
    assert _schema_digest(migrated_path) == migrated_digest

    legacy_path = tmp_path / "unversioned.db"
    with sqlite3.connect(legacy_path) as connection:
        connection.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO sentinel VALUES (1)")
    legacy_digest = _schema_digest(legacy_path)
    legacy_engine = create_async_engine(
        f"sqlite+aiosqlite:///{legacy_path}"
    )
    with pytest.raises(RuntimeError, match="current=unversioned"):
        asyncio.run(validate_schema(legacy_engine))
    asyncio.run(legacy_engine.dispose())
    assert _schema_digest(legacy_path) == legacy_digest
