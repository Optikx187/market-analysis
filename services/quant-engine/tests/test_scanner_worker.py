from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

from alembic import command
from alembic.config import Config

from app import main, scanner_worker
from app.config import settings
from app.scanner_store import ScannerStore

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def migrated_store(tmp_path: Path) -> ScannerStore:
    database = tmp_path / "worker.db"
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


def test_worker_completes_one_durable_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = migrated_store(tmp_path)
    queued = store.enqueue_manual(
        "worker-success",
        now=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
    )
    run_scan = AsyncMock(
        return_value={
            "scanned": 1,
            "signals_found": 1,
            "notifications_sent": 0,
            "signals": [],
        }
    )
    monkeypatch.setattr(main, "_run_scan", run_scan)

    worked = asyncio.run(scanner_worker.run_once(store, "worker-a"))
    persisted = store.get_run(str(queued["run_id"]))

    assert worked is True
    assert persisted["status"] == "completed"
    assert persisted["result"]["signals_found"] == 1
    run_scan.assert_awaited_once()


def test_worker_failure_is_retryable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = migrated_store(tmp_path)
    store.update_config(enabled=False)
    queued = store.enqueue_manual(
        "worker-retry",
        now=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
    )
    store.update_config(enabled=True)
    monkeypatch.setattr(
        main,
        "_run_scan",
        AsyncMock(side_effect=RuntimeError("deterministic failure")),
    )
    monkeypatch.setattr(settings, "SCANNER_RETRY_SECONDS", 0)

    worked = asyncio.run(scanner_worker.run_once(store, "worker-a"))
    failed = store.get_run(str(queued["run_id"]))
    retry = store.claim_next("worker-b")

    assert worked is True
    assert failed["status"] == "failed"
    assert failed["attempt_count"] == 1
    assert retry is not None
    assert retry["run_id"] == queued["run_id"]
    assert retry["attempt_count"] == 2


def test_market_hours_gate_leaves_scheduled_work_unclaimed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = migrated_store(tmp_path)
    store.update_config(market_hours_only=True)
    monkeypatch.setattr(scanner_worker, "_is_market_hours", lambda: False)

    worked = asyncio.run(scanner_worker.run_once(store, "worker-a"))

    assert worked is False
    assert store.status()["total_scans"] == 0


def test_market_hours_gate_does_not_block_manual_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = migrated_store(tmp_path)
    store.update_config(market_hours_only=True)
    queued = store.enqueue_manual(
        "manual-outside-hours",
        now=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
    )
    monkeypatch.setattr(scanner_worker, "_is_market_hours", lambda: False)
    monkeypatch.setattr(
        main,
        "_run_scan",
        AsyncMock(
            return_value={
                "scanned": 0,
                "signals_found": 0,
                "notifications_sent": 0,
                "signals": [],
            }
        ),
    )

    worked = asyncio.run(scanner_worker.run_once(store, "worker-a"))

    assert worked is True
    assert store.get_run(str(queued["run_id"]))["status"] == "completed"
