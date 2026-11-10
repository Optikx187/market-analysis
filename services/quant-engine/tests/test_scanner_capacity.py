from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
from alembic import command
from alembic.config import Config

from app import main
from app.config import settings
from app.main import DataQualityResponse
from app.scanner_store import ScannerStore

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-7000-8000-000000000001"
TICKER_COUNT = 250
SOAK_RUN_COUNT = 250


def migrated_store(tmp_path: Path) -> ScannerStore:
    database = tmp_path / "capacity.db"
    original = settings.BACKTEST_DATABASE_PATH
    settings.BACKTEST_DATABASE_PATH = str(database)
    try:
        command.upgrade(
            Config(str(SERVICE_ROOT / "alembic.ini")),
            "head",
        )
    finally:
        settings.BACKTEST_DATABASE_PATH = original
    store = ScannerStore(
        str(database),
        tenant_id=TENANT,
        user_key="capacity",
    )
    store.ensure_config(
        enabled=False,
        interval_minutes=15,
        market_hours_only=False,
    )
    return store


def quality(ticker: str) -> DataQualityResponse:
    return DataQualityResponse(
        ticker=ticker,
        asset_type="crypto",
        interval="1d",
        status="healthy",
        is_eligible=True,
        candle_count=201,
        latest_timestamp="2030-01-01T00:00:00+00:00",
        age_hours=0.0,
        stale=False,
        duplicate_timestamps=0,
        missing_periods=0,
        invalid_timestamps=0,
        invalid_ohlc=0,
        anomaly_count=0,
        issues=[],
    )


def test_scanner_capacity_handles_250_tickers_with_bounded_calls(
    monkeypatch,
) -> None:
    tickers = [f"T{index:03d}" for index in range(TICKER_COUNT)]
    assets = [
        {
            "ticker": ticker,
            "asset_type": "crypto",
            "is_active": True,
        }
        for ticker in tickers
    ]
    frame = pd.DataFrame({"close": range(201)})
    monkeypatch.setattr(
        main,
        "_get_scanner_store",
        lambda: type(
            "FakeScannerStore",
            (),
            {"get_action": staticmethod(lambda _opportunity_id: None)},
        )(),
    )
    monkeypatch.setattr(
        main,
        "_fetch_assets",
        AsyncMock(return_value=assets),
    )
    monkeypatch.setattr(
        main,
        "_fetch_all_data_quality",
        AsyncMock(
            return_value={
                ticker: quality(ticker) for ticker in tickers
            }
        ),
    )
    monkeypatch.setattr(
        main,
        "fetch_candles_from_service_a",
        AsyncMock(return_value=frame),
    )
    monkeypatch.setattr(
        main,
        "evaluate_signals",
        lambda _frame, _capital: SimpleNamespace(
            direction="BUY",
            status="Oversold",
            trigger_price=100.0,
            stop_loss=95.0,
            target_price=115.0,
            reason="Deterministic capacity fixture",
            risk_reward=3.0,
            atr_value=2.0,
            rsi_value=25.0,
            suppressed=False,
            kelly_pct=0.1,
            optimal_size_usd=500.0,
            volatility_scalar=1.0,
        ),
    )
    monkeypatch.setattr(
        main,
        "_fetch_upcoming_earnings",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        main,
        "_latest_backtest_result",
        lambda _ticker: {
            "aggregate": {
                "out_of_sample": {
                    "total_trades": 30,
                    "expectancy_pct": 1.0,
                    "win_rate_pct": 60.0,
                }
            },
            "alert_eligibility": {"eligible": True, "reasons": []},
        },
    )
    monkeypatch.setattr(
        main,
        "_process_signal_via_portfolio",
        AsyncMock(
            return_value={
                "approved": False,
                "reason": "Capacity fixture rejects execution",
                "risk_decision": {
                    "action": "rejected",
                    "approved": False,
                },
            }
        ),
    )
    monkeypatch.setattr(
        main,
        "_send_notification",
        AsyncMock(return_value=False),
    )

    result = asyncio.run(main._run_scan(schedule_name="capacity"))

    assert result["scanned"] == TICKER_COUNT
    assert result["signals_found"] == TICKER_COUNT
    assert result["errors"] == 0
    assert result["provider_metrics"]["data-ingestion"]["calls"] == (
        3 + TICKER_COUNT * 3
    )
    assert result["provider_metrics"]["portfolio-engine"]["calls"] == (
        TICKER_COUNT
    )


def test_two_worker_soak_recovers_killed_lease_without_duplicates(
    tmp_path: Path,
) -> None:
    scanner = migrated_store(tmp_path)
    started_at = datetime.fromisoformat("2030-01-01T00:00:00+00:00")
    abandoned = scanner.enqueue_manual("capacity-000", now=started_at)
    assert scanner.claim_run(
        str(abandoned["run_id"]),
        "worker:killed:100:dead",
        now=started_at,
        lease_seconds=1,
    )
    for index in range(1, SOAK_RUN_COUNT):
        scanner.enqueue_manual(
            f"capacity-{index:03d}",
            now=started_at,
        )
    recovery_time = started_at + timedelta(seconds=2)
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    completed_ids: list[str] = []
    owners: list[str] = []

    def consume(owner: str) -> None:
        worker = ScannerStore(
            scanner.database_url,
            tenant_id=TENANT,
            user_key="capacity",
        )
        barrier.wait()
        while True:
            run = worker.claim_next(
                owner,
                now=recovery_time,
                lease_seconds=30,
                allow_scheduled=False,
            )
            if run is None:
                return
            worker.complete(
                str(run["run_id"]),
                owner,
                {
                    "signals_found": 0,
                    "signals": [],
                    "provider_metrics": {},
                },
                now=recovery_time + timedelta(seconds=1),
            )
            with lock:
                completed_ids.append(str(run["run_id"]))
                owners.append(owner)
            time.sleep(0.001)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                consume,
                f"worker:capacity:{index}:active",
            )
            for index in (1, 2)
        ]
        for future in futures:
            future.result(timeout=60)

    observed = scanner.observability(
        now=recovery_time + timedelta(seconds=2),
        window_minutes=15,
        max_attempts=3,
    )
    recovered = scanner.get_run(str(abandoned["run_id"]))

    assert len(completed_ids) == SOAK_RUN_COUNT
    assert len(set(completed_ids)) == SOAK_RUN_COUNT
    assert set(owners) == {
        "worker:capacity:1:active",
        "worker:capacity:2:active",
    }
    assert recovered["status"] == "completed"
    assert recovered["attempt_count"] == 2
    assert observed["queue"]["depth"] == 0
    assert observed["leases"]["running"] == 0
    assert observed["dead_jobs"] == 0
    assert observed["duplicate_idempotency_keys"] == 0
    assert observed["throughput"]["completed"] == SOAK_RUN_COUNT
    assert observed["throughput"]["retry_attempts"] == 1
