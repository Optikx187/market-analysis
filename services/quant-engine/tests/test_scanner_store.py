from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from app.scanner_store import ScannerStore

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-7000-8000-000000000001"


def store(tmp_path: Path) -> ScannerStore:
    database = tmp_path / "scanner.db"
    if not database.exists():
        original = settings.BACKTEST_DATABASE_PATH
        settings.BACKTEST_DATABASE_PATH = str(database)
        config = Config(str(SERVICE_ROOT / "alembic.ini"))
        try:
            command.upgrade(config, "head")
        finally:
            settings.BACKTEST_DATABASE_PATH = original
    result = ScannerStore(
        str(database),
        tenant_id=TENANT,
        user_key="tester",
    )
    result.ensure_config(
        enabled=True,
        interval_minutes=15,
        market_hours_only=False,
        now=datetime.fromisoformat("2030-01-01T00:00:00+00:00"),
    )
    return result


def test_configuration_and_completed_result_survive_restart(
    tmp_path: Path,
) -> None:
    first = store(tmp_path)
    first.update_config(interval_minutes=30, market_hours_only=True)
    queued = first.enqueue_manual("manual-1")
    claimed = first.claim_run(str(queued["run_id"]), "worker-a")
    assert claimed is not None
    first.complete(
        str(claimed["run_id"]),
        "worker-a",
        {"signals_found": 2, "signals": [{"ticker": "BTC"}]},
    )

    restarted = ScannerStore(
        first.database_url,
        tenant_id=TENANT,
        user_key="tester",
    )
    status = restarted.status()

    assert status["interval_minutes"] == 30
    assert status["market_hours_only"] is True
    assert status["last_scan_result"]["signals_found"] == 2
    assert status["latest_signal_count"] == 1


def test_only_one_concurrent_worker_claims_run(tmp_path: Path) -> None:
    primary = store(tmp_path)
    primary.enqueue_manual("claim-once")
    barrier = threading.Barrier(2)
    claimed: list[dict[str, object] | None] = []

    def claim(owner: str) -> None:
        contender = ScannerStore(
            primary.database_url,
            tenant_id=TENANT,
            user_key="tester",
        )
        barrier.wait()
        claimed.append(contender.claim_next(owner))

    threads = [
        threading.Thread(target=claim, args=("worker-a",)),
        threading.Thread(target=claim, args=("worker-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(item is not None for item in claimed) == 1


def test_expired_lease_is_recovered_without_duplicate_run(
    tmp_path: Path,
) -> None:
    scanner = store(tmp_path)
    scanner.update_config(enabled=False)
    start = datetime.fromisoformat("2030-01-01T01:00:00+00:00")
    queued = scanner.enqueue_manual("recoverable", now=start)
    first = scanner.claim_run(
        str(queued["run_id"]),
        "worker-a",
        now=start,
        lease_seconds=10,
    )
    assert first is not None

    takeover = scanner.claim_next(
        "worker-b",
        now=start + timedelta(seconds=11),
        lease_seconds=10,
    )

    assert takeover is not None
    assert takeover["run_id"] == first["run_id"]
    assert takeover["attempt_count"] == 2


def test_worker_heartbeat_tracks_staleness_and_counters(
    tmp_path: Path,
) -> None:
    scanner = store(tmp_path)
    start = datetime.fromisoformat("2030-01-01T00:00:00+00:00")
    scanner.touch_worker(
        "worker:host-a:101:one",
        hostname="host-a",
        process_id=101,
        status="running",
        current_run_id="00000000-0000-7000-8000-000000000010",
        current_scanner_key=scanner.scanner_key,
        now=start,
    )

    fresh = scanner.worker_health(
        now=start + timedelta(seconds=5),
        stale_after_seconds=30,
    )[0]
    stale = scanner.worker_health(
        now=start + timedelta(seconds=31),
        stale_after_seconds=30,
    )[0]
    scanner.touch_worker(
        "worker:host-a:101:one",
        hostname="host-a",
        process_id=101,
        status="idle",
        completed_delta=1,
        last_run_duration_ms=250,
        now=start + timedelta(seconds=32),
    )
    completed = scanner.worker_health(
        now=start + timedelta(seconds=33),
        stale_after_seconds=30,
    )[0]

    assert fresh["stale"] is False
    assert fresh["current_scanner_key"] == scanner.scanner_key
    assert stale["stale"] is True
    assert completed["status"] == "idle"
    assert completed["completed_runs"] == 1
    assert completed["last_run_duration_ms"] == 250
    assert completed["current_run_id"] is None


def test_observability_identifies_lag_expired_leases_and_dead_jobs(
    tmp_path: Path,
) -> None:
    scanner = store(tmp_path)
    scanner.update_config(enabled=False)
    now = datetime.fromisoformat("2030-01-01T01:00:00+00:00")

    completed_run = scanner.enqueue_manual(
        "completed-observed",
        now=now - timedelta(seconds=100),
    )
    completed_claim = scanner.claim_run(
        str(completed_run["run_id"]),
        "worker-complete",
        now=now - timedelta(seconds=100),
    )
    assert completed_claim is not None
    scanner.complete(
        str(completed_run["run_id"]),
        "worker-complete",
        {
            "signals_found": 2,
            "signals": [],
            "provider_metrics": {
                "data-ingestion": {
                    "calls": 4,
                    "errors": 1,
                    "total_latency_ms": 80,
                    "max_latency_ms": 30,
                }
            },
        },
        now=now - timedelta(seconds=95),
    )

    dead_run = scanner.enqueue_manual(
        "dead-observed",
        now=now - timedelta(seconds=90),
    )
    for offset in (90, 80, 70):
        owner = f"worker-dead-{offset}"
        claimed = scanner.claim_run(
            str(dead_run["run_id"]),
            owner,
            now=now - timedelta(seconds=offset),
            max_attempts=3,
        )
        assert claimed is not None
        scanner.fail(
            str(dead_run["run_id"]),
            owner,
            "deterministic failure",
            now=now - timedelta(seconds=offset),
            retry_seconds=0,
        )

    scanner.enqueue_manual(
        "lagged-observed",
        now=now - timedelta(seconds=600),
    )
    expired_run = scanner.enqueue_manual(
        "expired-observed",
        now=now - timedelta(seconds=60),
    )
    assert scanner.claim_run(
        str(expired_run["run_id"]),
        "worker-expired",
        now=now - timedelta(seconds=60),
        lease_seconds=10,
    )

    metrics = scanner.observability(
        now=now,
        window_minutes=15,
        max_attempts=3,
    )

    assert metrics["queue"]["depth"] == 1
    assert metrics["queue"]["oldest_age_seconds"] == 600
    assert metrics["leases"] == {"running": 1, "expired": 1}
    assert metrics["dead_jobs"] == 1
    assert metrics["throughput"]["completed"] == 1
    assert metrics["throughput"]["failed"] == 1
    assert metrics["throughput"]["error_rate_pct"] == 50
    assert metrics["duration_ms"]["p95"] == 5000
    assert metrics["slowest_runs"][0] == {
        "correlation_id": completed_run["run_id"],
        "run_id": completed_run["run_id"],
        "duration_ms": 5000.0,
        "scanned": 0,
    }
    assert metrics["provider_metrics"]["data-ingestion"] == {
        "calls": 4,
        "errors": 1,
        "average_latency_ms": 20.0,
        "max_latency_ms": 30.0,
    }
    assert {
        problem["run_id"] for problem in metrics["problem_runs"]
    } == {
        dead_run["run_id"],
        expired_run["run_id"],
    }


def test_missed_intervals_coalesce_to_one_catch_up_run(
    tmp_path: Path,
) -> None:
    scanner = store(tmp_path)
    restart = datetime.fromisoformat("2030-01-03T00:00:00+00:00")

    first = scanner.claim_next("worker-restarted", now=restart)
    second = scanner.claim_next("worker-other", now=restart)
    config = scanner.get_config()

    assert first is not None
    assert first["trigger_type"] == "scheduled"
    assert second is None
    assert datetime.fromisoformat(
        str(config["next_run_at"])
    ) > restart


def test_retry_and_idempotency_reuse_same_run(tmp_path: Path) -> None:
    scanner = store(tmp_path)
    scanner.update_config(enabled=False)
    start = datetime.fromisoformat("2030-01-01T02:00:00+00:00")
    queued = scanner.enqueue_manual("retry-key", now=start)
    first = scanner.claim_run(
        str(queued["run_id"]),
        "worker-a",
        now=start,
    )
    assert first is not None
    scanner.fail(
        str(first["run_id"]),
        "worker-a",
        "temporary",
        now=start,
        retry_seconds=0,
    )
    retry = scanner.claim_next("worker-b", now=start)
    assert retry is not None
    scanner.complete(
        str(retry["run_id"]),
        "worker-b",
        {"signals_found": 1, "signals": []},
        now=start,
    )

    repeated = scanner.enqueue_manual("retry-key", now=start)

    assert repeated["run_id"] == queued["run_id"]
    assert repeated["status"] == "completed"
    assert repeated["attempt_count"] == 2


def test_lease_mutations_are_tenant_scoped(tmp_path: Path) -> None:
    scanner = store(tmp_path)
    scanner.update_config(enabled=False)
    queued = scanner.enqueue_manual("tenant-scope")
    claimed = scanner.claim_run(str(queued["run_id"]), "shared-owner")
    assert claimed is not None
    other_tenant = ScannerStore(
        scanner.database_url,
        tenant_id="00000000-0000-7000-8000-000000000099",
        user_key="tester",
    )
    other_tenant.ensure_config(
        enabled=False,
        interval_minutes=15,
        market_hours_only=False,
    )

    assert not other_tenant.heartbeat(
        str(queued["run_id"]),
        "shared-owner",
    )
    with pytest.raises(RuntimeError, match="lease was lost"):
        other_tenant.complete(
            str(queued["run_id"]),
            "shared-owner",
            {"signals_found": 0, "signals": []},
        )
    assert scanner.get_run(str(queued["run_id"]))["status"] == "running"


def test_actions_and_notification_receipts_are_durable(
    tmp_path: Path,
) -> None:
    scanner = store(tmp_path)
    run = scanner.enqueue_manual("notification-run")
    sent_at = datetime.fromisoformat("2030-01-01T00:00:00+00:00")
    scanner.save_action(
        "BTC:BUY",
        "snooze",
        {
            "user_decision": "snoozed",
            "snoozed_until": "2030-01-02T00:00:00+00:00",
        },
    )

    assert scanner.get_action("BTC:BUY")["user_decision"] == "snoozed"
    assert scanner.claim_notification(
        str(run["run_id"]),
        "BTC:BUY",
        "signal:BTC:BUY",
        now=sent_at,
    )
    assert not scanner.claim_notification(
        str(run["run_id"]),
        "BTC:BUY",
        "signal:BTC:BUY",
        now=sent_at + timedelta(hours=1),
    )
    scanner.finish_notification(
        "signal:BTC:BUY",
        sent=False,
        error="temporary",
        now=sent_at + timedelta(hours=1),
    )
    assert scanner.claim_notification(
        str(run["run_id"]),
        "BTC:BUY",
        "signal:BTC:BUY",
        now=sent_at + timedelta(hours=1),
    )
    scanner.finish_notification(
        "signal:BTC:BUY",
        sent=True,
        now=sent_at + timedelta(hours=1),
    )
    assert not scanner.claim_notification(
        str(run["run_id"]),
        "BTC:BUY",
        "signal:BTC:BUY",
        now=sent_at + timedelta(hours=23),
    )
    assert scanner.claim_notification(
        str(run["run_id"]),
        "BTC:BUY",
        "signal:BTC:BUY",
        now=sent_at + timedelta(hours=26),
    )
