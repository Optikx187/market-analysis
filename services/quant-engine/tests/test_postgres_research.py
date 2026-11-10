import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from app.backtest_store import BacktestStore
from app.scanner_store import ScannerStore

DATABASE_URL = os.environ.get("POSTGRES_RESEARCH_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="POSTGRES_RESEARCH_TEST_DATABASE_URL is not configured",
)


def _psycopg_url() -> str:
    assert DATABASE_URL is not None
    return DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def _result(*, eligible: bool, padding: int = 0) -> dict[str, object]:
    return {
        "ticker": "BTC",
        "alert_eligibility": {
            "eligible": eligible,
            "reasons": [] if eligible else ["deterministic fixture"],
        },
        "aggregate": {
            "out_of_sample": {
                "total_return_pct": 4.25,
                "total_trades": 12,
            },
        },
        "trades": [{"notes": "x" * padding}] if padding else [],
    }


def test_postgresql_research_run_is_reproducible_and_artifact_bounded(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    store = BacktestStore(
        DATABASE_URL,
        artifact_directory=tmp_path / "artifacts",
        user_key="researcher",
        max_inline_result_bytes=1024,
    )
    lineage = {
        "source_service": "data-ingestion",
        "primary": {
            "ticker": "BTC",
            "interval": "1d",
            "row_count": 400,
            "first_observed_at": "2025-01-01T00:00:00+00:00",
            "last_observed_at": "2026-02-04T00:00:00+00:00",
            "content_sha256": "a" * 64,
        },
        "benchmarks": [],
    }

    run_id = store.save(
        "BTC",
        "ema-rsi-atr-v2",
        {"ticker": "BTC", "period": "max"},
        _result(eligible=True, padding=10_000),
        input_lineage=lineage,
    )
    loaded = store.get(run_id)

    assert uuid.UUID(run_id).version == 7
    assert loaded is not None
    assert loaded["ticker"] == "BTC"
    assert loaded["module_key"] == "walk-forward-backtest"
    assert loaded["module_version"] == "walk-forward-v1"
    assert loaded["input_lineage"] == lineage
    assert store.latest("BTC") == loaded
    assert store.list("BTC") == [{
        "run_id": run_id,
        "ticker": "BTC",
        "strategy_version": "ema-rsi-atr-v2",
        "created_at": loaded["created_at"],
        "alert_eligible": True,
    }]

    with psycopg.connect(_psycopg_url()) as connection:
        row = connection.execute(
            """
            SELECT request_json, result_json, input_lineage_json,
                   artifact_manifest_json, retention_class,
                   retain_until - created_at
            FROM research.backtest_runs
            WHERE run_id = %s
            """,
            (run_id,),
        ).fetchone()
        artifact = connection.execute(
            """
            SELECT uri, sha256, byte_size, retain_until
            FROM research.backtest_artifacts
            WHERE run_id = %s
            """,
            (run_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == {"period": "max", "ticker": "BTC"}
    assert len(json.dumps(row[1]).encode()) < 1024
    assert row[2] == lineage
    assert row[3][0]["artifact_key"] == "full-result"
    assert row[4] == "recommendation-evidence"
    assert row[5].days in {2556, 2557}
    assert artifact is not None
    assert artifact[0] == row[3][0]["uri"]
    assert artifact[1] == row[3][0]["sha256"]
    assert artifact[2] == row[3][0]["byte_size"]


def test_postgresql_history_is_tenant_and_user_scoped(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    alice = BacktestStore(
        DATABASE_URL,
        artifact_directory=tmp_path / "alice",
        user_key="alice",
    )
    bob = BacktestStore(
        DATABASE_URL,
        artifact_directory=tmp_path / "bob",
        user_key="bob",
    )

    alice_run = alice.save(
        "ETH",
        "v1",
        {"ticker": "ETH"},
        _result(eligible=False),
    )
    bob_run = bob.save(
        "ETH",
        "v1",
        {"ticker": "ETH"},
        _result(eligible=False),
    )

    assert alice.get(alice_run) is not None
    assert alice.get(bob_run) is None
    assert bob.get(alice_run) is None
    assert [row["run_id"] for row in alice.list("ETH")] == [alice_run]
    assert [row["run_id"] for row in bob.list("ETH")] == [bob_run]
    with psycopg.connect(_psycopg_url()) as connection:
        retention = connection.execute(
            """
            SELECT retention_class, retain_until - created_at
            FROM research.backtest_runs
            WHERE run_id = %s
            """,
            (alice_run,),
        ).fetchone()
    assert retention is not None
    assert retention[0] == "ad-hoc-research"
    assert retention[1].days in {730, 731}


def test_completed_research_evidence_is_immutable(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    store = BacktestStore(
        DATABASE_URL,
        artifact_directory=tmp_path / "artifacts",
        user_key="auditor",
    )
    run_id = store.save(
        "AAPL",
        "v1",
        {"ticker": "AAPL"},
        _result(eligible=False),
    )

    with psycopg.connect(_psycopg_url()) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            connection.execute(
                "UPDATE research.backtest_runs "
                "SET strategy_version='changed' WHERE run_id=%s",
                (run_id,),
            )
        connection.rollback()
        persisted = connection.execute(
            "SELECT strategy_version FROM research.backtest_runs "
            "WHERE run_id=%s",
            (run_id,),
        ).fetchone()
    assert persisted == ("v1",)


def test_postgresql_scanner_claims_are_concurrent_and_tenant_scoped() -> None:
    assert DATABASE_URL is not None
    tenant_a = "00000000-0000-7000-8000-000000000011"
    tenant_b = "00000000-0000-7000-8000-000000000012"
    alice = ScannerStore(DATABASE_URL, tenant_id=tenant_a, user_key="alice")
    bob = ScannerStore(DATABASE_URL, tenant_id=tenant_b, user_key="bob")
    for store in (alice, bob):
        store.ensure_config(
            enabled=False,
            interval_minutes=15,
            market_hours_only=False,
        )
    alice_run = alice.enqueue_manual("shared-key")
    bob_run = bob.enqueue_manual("shared-key")

    alice_claim = alice.claim_next("worker-a")
    duplicate_claim = alice.claim_next("worker-b")
    bob_claim = bob.claim_next("worker-b")

    assert alice_claim is not None
    assert alice_claim["run_id"] == alice_run["run_id"]
    assert duplicate_claim is None
    assert bob_claim is not None
    assert bob_claim["run_id"] == bob_run["run_id"]
    assert bob_claim["run_id"] != alice_claim["run_id"]
    alice.complete(
        str(alice_claim["run_id"]),
        "worker-a",
        {"signals_found": 0, "signals": []},
    )
    bob.complete(
        str(bob_claim["run_id"]),
        "worker-b",
        {"signals_found": 0, "signals": []},
    )

    takeover_start = datetime.fromisoformat(
        "2030-02-01T00:00:00+00:00"
    )
    takeover_run = alice.enqueue_manual(
        "worker-kill-takeover",
        now=takeover_start,
    )
    abandoned = alice.claim_run(
        str(takeover_run["run_id"]),
        "worker-killed",
        now=takeover_start,
        lease_seconds=10,
    )
    recovered = alice.claim_next(
        "worker-recovery",
        now=takeover_start + timedelta(seconds=11),
        lease_seconds=10,
        allow_scheduled=False,
    )
    assert abandoned is not None
    assert recovered is not None
    assert recovered["run_id"] == abandoned["run_id"]
    assert recovered["attempt_count"] == 2
    alice.touch_worker(
        "worker:postgres:101:alice",
        hostname="postgres",
        process_id=101,
        status="running",
        current_run_id=str(recovered["run_id"]),
        current_scanner_key=alice.scanner_key,
        now=takeover_start + timedelta(seconds=11),
    )
    bob.touch_worker(
        "worker:postgres:202:bob",
        hostname="postgres",
        process_id=202,
        status="idle",
        now=takeover_start + timedelta(seconds=11),
    )
    alice_workers = alice.worker_health(
        now=takeover_start + timedelta(seconds=12),
    )
    assert [worker["worker_id"] for worker in alice_workers] == [
        "worker:postgres:101:alice"
    ]
    assert alice_workers[0]["current_run_id"] == recovered["run_id"]
    observed = alice.observability(
        now=takeover_start + timedelta(seconds=22),
        max_attempts=3,
    )
    assert observed["leases"]["expired"] == 1
    assert observed["problem_runs"][0]["correlation_id"] == recovered["run_id"]

    alice.save_action(
        "BTC:BUY",
        "approve",
        {"user_decision": "approved", "snoozed_until": None},
    )
    assert alice.get_action("BTC:BUY") == {
        "user_decision": "approved",
        "snoozed_until": None,
    }
    sent_at = datetime.fromisoformat("2030-01-01T00:00:00+00:00")
    assert alice.claim_notification(
        str(alice_run["run_id"]),
        "BTC:BUY",
        "signal:BTC:BUY:postgres",
        now=sent_at,
    )
    assert not alice.claim_notification(
        str(alice_run["run_id"]),
        "BTC:BUY",
        "signal:BTC:BUY:postgres",
        now=sent_at + timedelta(hours=1),
    )
    alice.finish_notification(
        "signal:BTC:BUY:postgres",
        sent=True,
        now=sent_at,
    )
    assert not alice.claim_notification(
        str(alice_run["run_id"]),
        "BTC:BUY",
        "signal:BTC:BUY:postgres",
        now=sent_at + timedelta(hours=23),
    )
    assert alice.claim_notification(
        str(alice_run["run_id"]),
        "BTC:BUY",
        "signal:BTC:BUY:postgres",
        now=sent_at + timedelta(hours=25),
    )
