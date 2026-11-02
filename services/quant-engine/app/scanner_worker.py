"""Dedicated durable scanner worker."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
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


async def _run_claimed(
    store: ScannerStore,
    claimed: dict[str, object],
    owner: str,
    schedule: AssetSchedule,
) -> None:
    run_id = str(claimed["run_id"])
    result = await _run_leased_scan(
        store,
        run_id,
        owner,
        asset_types=schedule.asset_types,
        schedule_name=schedule.name,
    )
    logger.info(
        "Scanner run %s completed: %s scanned, %s signals",
        run_id,
        result["scanned"],
        result["signals_found"],
    )


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
    try:
        await _run_claimed(store, claimed, owner, schedule)
    except asyncio.CancelledError:
        raise
    except Exception as error:
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
    logger.info("Scanner worker started as %s", owner)
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
            logger.exception("Scanner worker manual recovery polling failed")
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
            await asyncio.sleep(settings.SCANNER_WORKER_POLL_SECONDS)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
