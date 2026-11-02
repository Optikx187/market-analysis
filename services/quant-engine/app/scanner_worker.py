"""Dedicated durable scanner worker."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid

from app.config import settings
from app.main import _is_market_hours, _run_leased_scan
from app.migration_state import validate_schema
from app.scanner_store import ScannerStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _run_claimed(
    store: ScannerStore,
    claimed: dict[str, object],
    owner: str,
) -> None:
    run_id = str(claimed["run_id"])
    result = await _run_leased_scan(store, run_id, owner)
    logger.info(
        "Scanner run %s completed: %s scanned, %s signals",
        run_id,
        result["scanned"],
        result["signals_found"],
    )


async def run_once(store: ScannerStore, owner: str) -> bool:
    config = store.get_config()
    allow_scheduled = bool(config["enabled"]) and (
        not bool(config["market_hours_only"]) or _is_market_hours()
    )
    claimed = store.claim_next(
        owner,
        lease_seconds=settings.SCANNER_LEASE_SECONDS,
        max_attempts=settings.SCANNER_MAX_ATTEMPTS,
        allow_scheduled=allow_scheduled,
    )
    if claimed is None:
        return False
    run_id = str(claimed["run_id"])
    try:
        await _run_claimed(store, claimed, owner)
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
    store = ScannerStore(
        settings.research_database_url,
        tenant_id=settings.RESEARCH_TENANT_ID,
        user_key=settings.RESEARCH_USER_KEY,
    )
    store.ensure_config(
        enabled=settings.SCAN_ENABLED,
        interval_minutes=settings.SCAN_INTERVAL_MINUTES,
        market_hours_only=settings.MARKET_HOURS_ONLY,
    )
    owner = f"worker:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
    logger.info("Scanner worker started as %s", owner)
    while True:
        try:
            worked = await run_once(store, owner)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scanner worker polling failed")
            worked = False
        if not worked:
            await asyncio.sleep(settings.SCANNER_WORKER_POLL_SECONDS)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
