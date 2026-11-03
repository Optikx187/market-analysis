from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config

from app import main
from app.config import settings
from app.market_calendar import schedule_definitions
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


def schedule_stores(legacy: ScannerStore) -> dict[str, ScannerStore]:
    stores = {
        definition.name: ScannerStore(
            legacy.database_url,
            tenant_id=legacy.tenant_id,
            user_key=legacy.user_key,
            scanner_key=definition.scanner_key,
        )
        for definition in schedule_definitions("XNYS")
    }
    for name, store in stores.items():
        store.ensure_config(
            enabled=True,
            interval_minutes=15,
            market_hours_only=name == "equity",
        )
    return stores


def test_manual_scan_idempotency_and_latest_result_survive_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = migrated_store(tmp_path)
    monkeypatch.setattr(main, "_scanner_store", first)
    monkeypatch.setattr(main, "_schedule_stores", schedule_stores(first))
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
    monkeypatch.setattr(
        main,
        "_schedule_stores",
        schedule_stores(restarted),
    )
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
    monkeypatch.setattr(main, "_schedule_stores", schedule_stores(store))
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


def test_schedule_configuration_is_independent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    legacy = migrated_store(tmp_path)
    stores = schedule_stores(legacy)
    monkeypatch.setattr(main, "_scanner_store", legacy)
    monkeypatch.setattr(main, "_schedule_stores", stores)

    response = asyncio.run(
        main.update_scanner_config(
            main.ScanConfigUpdate(
                schedule="crypto",
                enabled=False,
                interval_minutes=5,
            )
        )
    )

    assert response["schedules"]["crypto"]["enabled"] is False
    assert response["schedules"]["crypto"]["interval_minutes"] == 5
    assert response["schedules"]["equity"]["enabled"] is True
    assert response["schedules"]["equity"]["interval_minutes"] == 15
    restarted = schedule_stores(legacy)
    assert restarted["crypto"].get_config()["enabled"] is False
    assert restarted["crypto"].get_config()["interval_minutes"] == 5


def test_status_combines_latest_equity_and_crypto_results(
    tmp_path: Path,
    monkeypatch,
) -> None:
    legacy = migrated_store(tmp_path)
    stores = schedule_stores(legacy)
    monkeypatch.setattr(main, "_scanner_store", legacy)
    monkeypatch.setattr(main, "_schedule_stores", stores)
    start = datetime.fromisoformat("2030-01-01T00:00:00+00:00")
    fixtures = {
        "equity": {
            "ticker": "AAPL",
            "score": 80,
        },
        "crypto": {
            "ticker": "BTC",
            "score": 70,
        },
    }
    for offset, (name, signal) in enumerate(fixtures.items()):
        store = stores[name]
        queued = store.enqueue_manual(f"{name}-result", now=start)
        claimed = store.claim_run(
            str(queued["run_id"]),
            f"worker-{name}",
            now=start,
        )
        assert claimed is not None
        store.complete(
            str(claimed["run_id"]),
            f"worker-{name}",
            {
                "scanned": 1,
                "signals_found": 1,
                "notifications_sent": 0,
                "errors": 0,
                "quality_rejected": 0,
                "quality_rejections": [],
                "signals": [signal],
                "timestamp": (
                    start + timedelta(minutes=offset)
                ).isoformat(),
            },
            now=start + timedelta(minutes=offset),
        )

    status = asyncio.run(main.scanner_status())

    assert status["last_scan_result"]["scanned"] == 2
    assert [
        signal["ticker"]
        for signal in status["last_scan_result"]["signals"]
    ] == ["AAPL", "BTC"]
    assert set(status["schedules"]) == {"equity", "crypto"}
