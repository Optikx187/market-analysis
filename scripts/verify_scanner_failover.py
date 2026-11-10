#!/usr/bin/env python3
"""Kill one real scanner process and prove lease-safe recovery."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services" / "quant-engine"
TIMEOUT_SECONDS = 45


def candles(interval: str) -> list[dict[str, object]]:
    duration = {
        "1d": timedelta(days=1),
        "4h": timedelta(hours=4),
        "1h": timedelta(hours=1),
    }[interval]
    latest = datetime.now(timezone.utc).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    rows = []
    for index in range(201):
        close = 100 + index * 0.25
        rows.append(
            {
                "timestamp": (
                    latest - duration * (200 - index)
                ).isoformat(),
                "open": close - 0.1,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1000 + index,
            }
        )
    return rows


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def respond(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        request = urlparse(self.path)
        if request.path == "/api/assets":
            self.respond(
                [
                    {
                        "ticker": "BTC",
                        "name": "Bitcoin",
                        "asset_type": "crypto",
                        "is_active": True,
                    }
                ]
            )
            return
        if request.path == "/api/data-quality":
            self.respond(
                {
                    "BTC": {
                        "ticker": "BTC",
                        "asset_type": "crypto",
                        "interval": "1d",
                        "status": "healthy",
                        "is_eligible": True,
                        "candle_count": 201,
                        "latest_timestamp": datetime.now(
                            timezone.utc
                        ).isoformat(),
                        "age_hours": 0.0,
                        "stale": False,
                        "duplicate_timestamps": 0,
                        "missing_periods": 0,
                        "invalid_timestamps": 0,
                        "invalid_ohlc": 0,
                        "anomaly_count": 0,
                        "issues": [],
                    }
                }
            )
            return
        if request.path == "/api/earnings/upcoming/all":
            self.respond([])
            return
        if request.path == "/api/candles/BTC":
            interval = parse_qs(request.query).get("interval", ["1d"])[0]
            time.sleep(2)
            self.respond(candles(interval))
            return
        self.respond({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        request = urlparse(self.path)
        if request.path == "/api/process-signal":
            self.respond(
                {
                    "approved": False,
                    "reason": "Deterministic failover fixture",
                    "risk_decision": {
                        "action": "rejected",
                        "approved": False,
                    },
                }
            )
            return
        if request.path == "/api/notify":
            self.respond({"success": True})
            return
        self.respond({"error": "not found"}, 404)


def wait_until(predicate, description: str):
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {description}")


def main() -> int:
    workers: list[subprocess.Popen[bytes]] = []
    server: ThreadingHTTPServer | None = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="scanner-failover-"
        ) as directory:
            database = Path(directory) / "research.db"
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = int(reservation.getsockname()[1])
            server = ThreadingHTTPServer(
                ("127.0.0.1", port),
                FixtureHandler,
            )
            thread = threading.Thread(
                target=server.serve_forever,
                daemon=True,
            )
            thread.start()
            database_url = f"sqlite:///{database}"
            environment = {
                **os.environ,
                "PYTHONPATH": ".",
                "BACKTEST_DATABASE_PATH": str(database),
                "RESEARCH_DATABASE_URL": database_url,
                "RESEARCH_STORAGE_BACKEND": "sqlite-transition",
                "SCAN_ENABLED": "false",
                "SCANNER_WORKER_POLL_SECONDS": "0.2",
                "SCANNER_LEASE_SECONDS": "3",
                "SCANNER_WORKER_STALE_SECONDS": "2",
                "SCANNER_RETRY_SECONDS": "0",
                "SCANNER_MAX_ATTEMPTS": "3",
                "DATA_INGESTION_URL": f"http://127.0.0.1:{port}",
                "PORTFOLIO_ENGINE_URL": f"http://127.0.0.1:{port}",
                "NOTIFICATION_GATEWAY_URL": f"http://127.0.0.1:{port}",
            }
            migration = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=SERVICE_ROOT,
                env=environment,
                capture_output=True,
                check=False,
            )
            if migration.returncode != 0:
                raise RuntimeError(
                    migration.stderr.decode(errors="replace")
                )
            os.environ.update(
                {
                    key: value
                    for key, value in environment.items()
                    if key
                    in {
                        "BACKTEST_DATABASE_PATH",
                        "RESEARCH_DATABASE_URL",
                        "RESEARCH_STORAGE_BACKEND",
                        "SCAN_ENABLED",
                        "SCANNER_WORKER_POLL_SECONDS",
                        "SCANNER_LEASE_SECONDS",
                        "SCANNER_WORKER_STALE_SECONDS",
                        "SCANNER_RETRY_SECONDS",
                        "SCANNER_MAX_ATTEMPTS",
                        "DATA_INGESTION_URL",
                        "PORTFOLIO_ENGINE_URL",
                        "NOTIFICATION_GATEWAY_URL",
                    }
                }
            )
            sys.path.insert(0, str(SERVICE_ROOT))
            from app.scanner_store import ScannerStore

            store = ScannerStore(database_url)
            store.ensure_config(
                enabled=False,
                interval_minutes=15,
                market_hours_only=False,
            )
            for _ in range(2):
                workers.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "app.scanner_worker",
                        ],
                        cwd=SERVICE_ROOT,
                        env=environment,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                )
            wait_until(
                lambda: (
                    store.worker_health(stale_after_seconds=2)
                    if len(
                        [
                            worker
                            for worker in store.worker_health(
                                stale_after_seconds=2
                            )
                            if not worker["stale"]
                            and worker["status"] != "stopped"
                        ]
                    )
                    == 2
                    else None
                ),
                "two active workers",
            )
            queued = store.enqueue_manual("process-kill-proof")

            def claimed():
                run = store.get_run(str(queued["run_id"]))
                return run if run["status"] == "running" else None

            running = wait_until(claimed, "initial worker claim")
            owner = str(running["lease_owner"])
            owner_pid = int(owner.split(":")[2])
            killed = next(
                process for process in workers if process.pid == owner_pid
            )
            killed.kill()
            killed.wait(timeout=10)

            def completed():
                run = store.get_run(str(queued["run_id"]))
                return run if run["status"] == "completed" else None

            recovered = wait_until(
                completed,
                "lease takeover and completion",
            )
            observed = store.observability(
                max_attempts=3,
                window_minutes=15,
            )
            health = store.worker_health(stale_after_seconds=2)
            survivor = next(
                process for process in workers if process.poll() is None
            )
            active = [
                worker
                for worker in health
                if worker["process_id"] == survivor.pid
                and not worker["stale"]
            ]
            stale = [
                worker
                for worker in health
                if worker["process_id"] == killed.pid
                and worker["stale"]
            ]
            if recovered["attempt_count"] != 2:
                raise RuntimeError("Recovered run did not record takeover")
            if len(active) != 1 or len(stale) != 1:
                raise RuntimeError("Worker heartbeat failover was not visible")
            if observed["duplicate_idempotency_keys"] != 0:
                raise RuntimeError("Duplicate durable work was detected")
            if observed["queue"]["depth"] != 0:
                raise RuntimeError("Recovered queue did not drain")
            print(
                json.dumps(
                    {
                        "run_id": recovered["run_id"],
                        "attempt_count": recovered["attempt_count"],
                        "killed_process_id": killed.pid,
                        "survivor_process_id": survivor.pid,
                        "active_workers": len(active),
                        "stale_workers": len(stale),
                        "queue_depth": observed["queue"]["depth"],
                        "duplicate_idempotency_keys": observed[
                            "duplicate_idempotency_keys"
                        ],
                        "result_summary": {
                            "scanned": recovered["result"]["scanned"],
                            "signals_found": recovered["result"][
                                "signals_found"
                            ],
                            "errors": recovered["result"]["errors"],
                            "duration_ms": recovered["result"][
                                "duration_ms"
                            ],
                            "correlation_id": recovered["result"][
                                "correlation_id"
                            ],
                            "provider_metrics": recovered["result"][
                                "provider_metrics"
                            ],
                        },
                    },
                    sort_keys=True,
                )
            )
            return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Scanner failover verification failed: {error}", file=sys.stderr)
        return 1
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=10)
        if server is not None:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
