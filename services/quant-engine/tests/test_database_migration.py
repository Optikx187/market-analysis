import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.backtest_store import BacktestStore
from app.config import settings
from app.migration_state import validate_schema


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _run(database_path: Path, revision: str, *, downgrade: bool = False) -> None:
    original_path = settings.BACKTEST_DATABASE_PATH
    settings.BACKTEST_DATABASE_PATH = str(database_path)
    try:
        config = Config(str(SERVICE_ROOT / "alembic.ini"))
        if downgrade:
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)
    finally:
        settings.BACKTEST_DATABASE_PATH = original_path


def _digest(database_path: Path) -> str:
    with sqlite3.connect(database_path) as connection:
        schema = connection.execute(
            "SELECT type, name, COALESCE(sql, '') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        rows = connection.execute(
            "SELECT * FROM backtest_runs ORDER BY run_id"
        ).fetchall() if any(row[1] == "backtest_runs" for row in schema) else []
    return hashlib.sha256(repr((schema, rows)).encode()).hexdigest()


def _create_legacy_fixture(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE backtest_runs ("
            "run_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, "
            "strategy_version TEXT NOT NULL, created_at TEXT NOT NULL, "
            "request_json TEXT NOT NULL, result_json TEXT NOT NULL, "
            "alert_eligible INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO backtest_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-run",
                "BTC",
                "v1",
                "2025-01-01T00:00:00+00:00",
                json.dumps({"ticker": "BTC"}),
                json.dumps({"ticker": "BTC", "return": 12.5}),
                1,
            ),
        )


def test_empty_database_upgrades_to_head_and_repeats_cleanly(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "empty.db"

    _run(database_path, "head")
    first_digest = _digest(database_path)
    _run(database_path, "head")

    assert _digest(database_path) == first_digest
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0002_research",)
        index_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='idx_backtest_runs_ticker_created'"
        ).fetchone()
        artifact_table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='backtest_artifacts'"
        ).fetchone()
    assert index_sql is not None
    assert "created_at DESC" in index_sql[0]
    assert artifact_table == ("backtest_artifacts",)


def test_legacy_backtest_history_is_preserved(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.db"
    _create_legacy_fixture(database_path)

    _run(database_path, "head")

    store = BacktestStore(str(database_path))
    assert store.get("legacy-run") == {
        "ticker": "BTC",
        "return": 12.5,
    }
    assert store.list("BTC") == [{
        "run_id": "legacy-run",
        "ticker": "BTC",
        "strategy_version": "v1",
        "created_at": "2025-01-01T00:00:00+00:00",
        "alert_eligible": True,
    }]
    with sqlite3.connect(database_path) as connection:
        metadata = connection.execute(
            "SELECT tenant_id, user_key, module_key, module_version, "
            "retention_class, retain_until FROM backtest_runs "
            "WHERE run_id='legacy-run'"
        ).fetchone()
    assert metadata == (
        "00000000-0000-7000-8000-000000000001",
        "default",
        "walk-forward-backtest",
        "legacy-v1",
        "legacy-research",
        "2032-01-01 00:00:00",
    )


def test_destructive_downgrade_requires_verified_restore(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "rollback.db"
    _run(database_path, "head")

    with pytest.raises(RuntimeError, match="verified pre-migration backup"):
        _run(database_path, "base", downgrade=True)

    validate_schema(str(database_path))


def test_validation_and_store_construction_do_not_mutate_bad_schema(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "unversioned.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO sentinel VALUES (1)")
    before = _digest(database_path)

    with pytest.raises(RuntimeError, match="current=unversioned"):
        validate_schema(str(database_path))
    with pytest.raises(RuntimeError, match="current=unversioned"):
        BacktestStore(str(database_path))

    assert _digest(database_path) == before


def test_stale_revision_fails_closed(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "stale.db"
    _run(database_path, "head")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE alembic_version SET version_num='stale-revision'"
        )
    before = _digest(database_path)

    with pytest.raises(RuntimeError, match="current=stale-revision"):
        validate_schema(str(database_path))

    assert _digest(database_path) == before
