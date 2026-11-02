from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config

from app import main
from app.config import settings
from app.scanner_store import ScannerStore

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def migrated_store(tmp_path: Path) -> ScannerStore:
    database = tmp_path / "scanner-api.db"
    original = settings.BACKTEST_DATABASE_PATH
    settings.BACKTEST_DATABASE_PATH = str(database)
    try:
        command.upgrade(Config(str(SERVICE_ROOT / "alembic.ini")), "head")
    finally:
        settings.BACKTEST_DATABASE_PATH = original
    store = ScannerStore(str(database))
    store.ensure_config(
        enabled=True,
        interval_minutes=15,
        market_hours_only=False,
    )
    return store


def test_manual_scan_idempotency_and_latest_result_survive_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = migrated_store(tmp_path)
    monkeypatch.setattr(main, "_scanner_store", first)
    run_scan = AsyncMock(
        return_value={
            "scanned": 1,
            "signals_found": 1,
            "notifications_sent": 0,
            "errors": 0,
            "quality_rejected": 0,
            "quality_rejections": [],
            "signals": [{"ticker": "BTC", "opportunity": {"id": "BTC:BUY"}}],
            "timestamp": "2030-01-01T00:00:00+00:00",
        }
    )
    monkeypatch.setattr(main, "_run_scan", run_scan)

    original = asyncio.run(main.scan_all("manual-api-key"))
    restarted = ScannerStore(
        first.database_url,
        tenant_id=first.tenant_id,
        user_key=first.user_key,
    )
    restarted.save_action(
        "BTC:BUY",
        "approve",
        {"user_decision": "approved", "snoozed_until": None},
    )
    monkeypatch.setattr(main, "_scanner_store", restarted)
    repeated = asyncio.run(main.scan_all("manual-api-key"))
    status = asyncio.run(main.scanner_status())

    assert original == repeated
    run_scan.assert_awaited_once()
    assert status["last_scan_result"]["signals"][0]["ticker"] == "BTC"
    assert status["runs_by_status"] == {"completed": 1}
    assert status["last_scan_result"]["signals"][0]["opportunity"][
        "user_decision"
    ] == "approved"


def test_api_has_no_process_local_scheduler_task() -> None:
    assert not hasattr(main, "_scanner_task")


def test_manual_scan_retries_same_failed_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = migrated_store(tmp_path)
    monkeypatch.setattr(main, "_scanner_store", store)
    monkeypatch.setattr(settings, "SCANNER_RETRY_SECONDS", 0)
    run_scan = AsyncMock(
        side_effect=[
            RuntimeError("temporary"),
            {
                "scanned": 0,
                "signals_found": 0,
                "notifications_sent": 0,
                "signals": [],
            },
        ]
    )
    monkeypatch.setattr(main, "_run_scan", run_scan)

    with pytest.raises(RuntimeError, match="temporary"):
        asyncio.run(main.scan_all("retry-manual-api"))
    completed = asyncio.run(main.scan_all("retry-manual-api"))
    durable = store.get_run_by_idempotency("retry-manual-api")

    assert completed["signals_found"] == 0
    assert durable["status"] == "completed"
    assert durable["attempt_count"] == 2
