"""Container health check for the durable scanner worker."""

from __future__ import annotations

import socket

from app.config import settings
from app.scanner_store import ScannerStore


def main() -> int:
    store = ScannerStore()
    workers = store.worker_health(
        stale_after_seconds=settings.SCANNER_WORKER_STALE_SECONDS,
    )
    hostname = socket.gethostname()
    healthy = any(
        worker["hostname"] == hostname
        and not worker["stale"]
        and worker["status"] != "stopped"
        for worker in workers
    )
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
