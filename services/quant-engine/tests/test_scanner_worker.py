from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

from alembic import command
from alembic.config import Config

from app import main, scanner_worker, scanner_worker_healthcheck
from app.config import settings
from app.market_calendar import schedule_definitions
from app.scanner_store import ScannerStore

SERVICE_ROOT = Path(__file__).resolve().parents[1]
EQUITY_SCHEDULE, CRYPTO_SCHEDULE = schedule_definitions("XNYS")


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

    worked = asyncio.run(
        scanner_worker.run_once(
            store,
            "worker-a",
            CRYPTO_SCHEDULE,
        )
    )
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

    worked = asyncio.run(
        scanner_worker.run_once(
            store,
            "worker-a",
            CRYPTO_SCHEDULE,
        )
    )
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
) -> None:
    store = migrated_store(tmp_path)
    store.update_config(market_hours_only=True)
    holiday = datetime.fromisoformat("2027-07-05T15:00:00+00:00")

    worked = asyncio.run(
        scanner_worker.run_once(
            store,
            "worker-a",
            EQUITY_SCHEDULE,
            now=holiday,
        )
    )

    assert worked is False
    assert store.status()["total_scans"] == 0
    assert store.get_config()["next_run_at"].startswith(
        "2027-07-06T13:30:00"
    )


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

    worked = asyncio.run(
        scanner_worker.run_once(
            store,
            "worker-a",
            EQUITY_SCHEDULE,
            now=datetime.fromisoformat(
                "2027-07-05T15:00:00+00:00"
            ),
        )
    )

    assert worked is True
    assert store.get_run(str(queued["run_id"]))["status"] == "completed"


def test_crypto_schedule_runs_during_equity_holiday(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = migrated_store(tmp_path)
    run_scan = AsyncMock(
        return_value={
            "scanned": 1,
            "signals_found": 1,
            "notifications_sent": 0,
            "signals": [],
        }
    )
    monkeypatch.setattr(main, "_run_scan", run_scan)

    worked = asyncio.run(
        scanner_worker.run_once(
            store,
            "worker-crypto",
            CRYPTO_SCHEDULE,
            now=datetime.fromisoformat(
                "2027-07-05T15:00:00+00:00"
            ),
        )
    )

    assert worked is True
    assert store.status()["total_scans"] == 1
    run_scan.assert_awaited_once()
    assert run_scan.await_args.kwargs["asset_types"] == frozenset(
        {"crypto"}
    )
    assert run_scan.await_args.kwargs["schedule_name"] == "crypto"


def test_worker_emits_correlation_logs_and_updates_heartbeat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = migrated_store(tmp_path)
    queued = store.enqueue_manual(
        "worker-observed",
        now=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
    )
    monkeypatch.setattr(
        main,
        "_run_scan",
        AsyncMock(
            return_value={
                "scanned": 2,
                "signals_found": 1,
                "notifications_sent": 0,
                "signals": [],
            }
        ),
    )
    owner = "worker:test-host:123:observed"
    messages: list[str] = []
    monkeypatch.setattr(
        scanner_worker.logger,
        "info",
        lambda message, *args: messages.append(
            message % args if args else message
        ),
    )

    worked = asyncio.run(
        scanner_worker.run_once(
            store,
            owner,
            CRYPTO_SCHEDULE,
        )
    )

    events = [
        json.loads(message)
        for message in messages
        if message.startswith("{")
    ]
    worker = store.worker_health()[0]

    assert worked is True
    assert [event["event"] for event in events] == [
        "scanner_run_claimed",
        "scanner_run_completed",
    ]
    assert all(
        event["correlation_id"] == queued["run_id"]
        for event in events
    )
    assert worker["worker_id"] == owner
    assert worker["status"] == "idle"
    assert worker["completed_runs"] == 1
    assert worker["current_run_id"] is None


def test_worker_container_healthcheck_requires_fresh_local_heartbeat(
    monkeypatch,
) -> None:
    class FakeStore:
        workers: list[dict[str, object]] = []

        def worker_health(self, **_kwargs):
            return self.workers

    fake = FakeStore()
    monkeypatch.setattr(
        scanner_worker_healthcheck,
        "ScannerStore",
        lambda: fake,
    )
    monkeypatch.setattr(
        scanner_worker_healthcheck.socket,
        "gethostname",
        lambda: "worker-host",
    )

    fake.workers = [
        {
            "hostname": "worker-host",
            "stale": False,
            "status": "idle",
        }
    ]
    assert scanner_worker_healthcheck.main() == 0

    fake.workers = [
        {
            "hostname": "worker-host",
            "stale": True,
            "status": "running",
        }
    ]
    assert scanner_worker_healthcheck.main() == 1


def test_telemetry_failure_does_not_reclassify_completed_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = migrated_store(tmp_path)
    queued = store.enqueue_manual(
        "telemetry-failure",
        now=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
    )
    monkeypatch.setattr(
        main,
        "_run_scan",
        AsyncMock(
            return_value={
                "scanned": 1,
                "signals_found": 0,
                "notifications_sent": 0,
                "signals": [],
            }
        ),
    )
    def fail_telemetry(*_args, **_kwargs) -> None:
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(store, "touch_worker", fail_telemetry)

    worked = asyncio.run(
        scanner_worker.run_once(
            store,
            "worker:test-host:123:telemetry",
            CRYPTO_SCHEDULE,
        )
    )

    assert worked is True
    assert store.get_run(str(queued["run_id"]))["status"] == "completed"
