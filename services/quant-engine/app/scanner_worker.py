"""Dedicated durable scanner worker."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.main import (
    _get_scanner_store,
    _run_leased_scan,
    initialize_scanner_stores,
)
from app.market_calendar import (
    AssetSchedule,
    schedule_decision,
    schedule_definitions,
)
from app.migration_state import validate_schema
from app.scanner_store import SCANNER_KEY, ScannerStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
UTC_ZONE = ZoneInfo("UTC")


def _worker_process(worker_id: str) -> tuple[str, int]:
    parts = worker_id.split(":")
    if len(parts) >= 4 and parts[0] == "worker":
        try:
            return parts[1], int(parts[2])
        except ValueError:
            pass
    return socket.gethostname(), os.getpid()


def _log_event(event: str, **fields: object) -> None:
    logger.info(
        json.dumps(
            {"event": event, **fields},
            sort_keys=True,
            default=str,
        )
    )


def _touch_worker(
    store: ScannerStore,
    worker_id: str,
    **values: object,
) -> None:
    try:
        store.touch_worker(worker_id, **values)
    except Exception:
        logger.exception(
            "Scanner worker heartbeat update failed for %s",
            worker_id,
        )


async def _run_claimed(
    store: ScannerStore,
    claimed: dict[str, object],
    owner: str,
    schedule: AssetSchedule,
) -> dict[str, object]:
    run_id = str(claimed["run_id"])
    result = await _run_leased_scan(
        store,
        run_id,
        owner,
        asset_types=schedule.asset_types,
        schedule_name=schedule.name,
    )
    return result


async def run_once(
    store: ScannerStore,
    owner: str,
    schedule: AssetSchedule,
    *,
    now: datetime | None = None,
    allow_schedule_creation: bool = True,
) -> bool:
    config = store.get_config()
    current = now or datetime.now(UTC_ZONE)
    decision = schedule_decision(current, schedule.calendar_name)
    if not decision.eligible and decision.next_eligible_at is not None:
        store.defer_until(decision.next_eligible_at, now=current)
    allow_scheduled = (
        allow_schedule_creation
        and bool(config["enabled"])
        and decision.eligible
    )
    claimed = store.claim_next(
        owner,
        now=current,
        lease_seconds=settings.SCANNER_LEASE_SECONDS,
        max_attempts=settings.SCANNER_MAX_ATTEMPTS,
        allow_scheduled=allow_scheduled,
    )
    if claimed is None:
        return False
    run_id = str(claimed["run_id"])
    hostname, process_id = _worker_process(owner)
    started = time.perf_counter()
    if owner.startswith("worker:"):
        _touch_worker(
            store,
            owner,
            hostname=hostname,
            process_id=process_id,
            status="running",
            current_run_id=run_id,
            current_scanner_key=store.scanner_key,
            now=current,
        )
    _log_event(
        "scanner_run_claimed",
        worker_id=owner,
        correlation_id=run_id,
        scanner_key=store.scanner_key,
        schedule=schedule.name,
        attempt_count=claimed["attempt_count"],
    )
    try:
        result = await _run_claimed(store, claimed, owner, schedule)
        duration_ms = round((time.perf_counter() - started) * 1000)
        if owner.startswith("worker:"):
            _touch_worker(
                store,
                owner,
                hostname=hostname,
                process_id=process_id,
                status="idle",
                completed_delta=1,
                last_run_duration_ms=duration_ms,
            )
        _log_event(
            "scanner_run_completed",
            worker_id=owner,
            correlation_id=run_id,
            scanner_key=store.scanner_key,
            schedule=schedule.name,
            duration_ms=duration_ms,
            scanned=result["scanned"],
            signals_found=result["signals_found"],
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        duration_ms = round((time.perf_counter() - started) * 1000)
        logger.exception("Scanner run %s failed", run_id)
        try:
            store.fail(
                run_id,
                owner,
                str(error),
                retry_seconds=settings.SCANNER_RETRY_SECONDS,
            )
        except RuntimeError:
            logger.warning(
                "Scanner run %s failure was not recorded because its lease "
                "is no longer owned by %s",
                run_id,
                owner,
            )
        if owner.startswith("worker:"):
            _touch_worker(
                store,
                owner,
                hostname=hostname,
                process_id=process_id,
                status="error",
                failed_delta=1,
                last_run_duration_ms=duration_ms,
                last_error=str(error),
            )
        _log_event(
            "scanner_run_failed",
            worker_id=owner,
            correlation_id=run_id,
            scanner_key=store.scanner_key,
            schedule=schedule.name,
            duration_ms=duration_ms,
            error=str(error),
        )
    return True


async def run_worker() -> None:
    validate_schema(settings.research_database_url)
    stores = initialize_scanner_stores()
    definitions = schedule_definitions(
        settings.EQUITY_EXCHANGE_CALENDAR
    )
    manual_schedule = AssetSchedule(
        name="manual",
        scanner_key=SCANNER_KEY,
        asset_types=frozenset({"stock", "crypto"}),
        calendar_name=None,
    )
    manual_store = _get_scanner_store()
    owner = f"worker:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
    hostname = socket.gethostname()
    process_id = os.getpid()
    _touch_worker(
        manual_store,
        owner,
        hostname=hostname,
        process_id=process_id,
        status="idle",
    )
    _log_event(
        "scanner_worker_started",
        worker_id=owner,
        hostname=hostname,
        process_id=process_id,
    )
    try:
        while True:
            worked = False
            try:
                worked = await run_once(
                    manual_store,
                    owner,
                    manual_schedule,
                    allow_schedule_creation=False,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Scanner worker manual recovery polling failed"
                )
            for schedule in definitions:
                try:
                    worked = (
                        await run_once(
                            stores[schedule.name],
                            owner,
                            schedule,
                        )
                        or worked
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Scanner worker polling failed for %s",
                        schedule.name,
                    )
            if not worked:
                _touch_worker(
                    manual_store,
                    owner,
                    hostname=hostname,
                    process_id=process_id,
                    status="idle",
                )
                await asyncio.sleep(
                    settings.SCANNER_WORKER_POLL_SECONDS
                )
    finally:
        _touch_worker(
            manual_store,
            owner,
            hostname=hostname,
            process_id=process_id,
            status="stopped",
        )
        _log_event("scanner_worker_stopped", worker_id=owner)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
