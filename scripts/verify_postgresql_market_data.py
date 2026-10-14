#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services" / "data-ingestion"
DEFAULT_IMAGE = "postgres:16-alpine"


class MarketDataVerificationError(RuntimeError):
    pass


def run(
    arguments: list[str],
    *,
    cwd: Path = ROOT,
    environment: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        raise MarketDataVerificationError(
            f"{' '.join(arguments[:3])} failed with exit code "
            f"{result.returncode}\n{output}"
        )
    return result


def wait_for_postgresql(container_name: str, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_output = ""
    while time.monotonic() < deadline:
        result = run(
            [
                "docker",
                "exec",
                container_name,
                "pg_isready",
                "--username",
                "postgres",
                "--dbname",
                "market_data_test",
            ],
            check=False,
        )
        last_output = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode == 0:
            return
        time.sleep(1)
    raise MarketDataVerificationError(
        f"PostgreSQL did not become ready within {timeout_seconds:.0f}s: "
        f"{last_output}"
    )


def mapped_port(container_name: str) -> str:
    result = run(["docker", "port", container_name, "5432/tcp"])
    endpoint = result.stdout.strip().splitlines()[0]
    return endpoint.rsplit(":", maxsplit=1)[1]


def main() -> int:
    image = os.environ.get("POSTGRES_MARKET_DATA_IMAGE", DEFAULT_IMAGE)
    container_name = f"market-analysis-pg-market-{uuid.uuid4().hex[:12]}"
    started = False
    try:
        run(["docker", "version"])
        run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                container_name,
                "--env",
                "POSTGRES_PASSWORD=market-data-test-only",
                "--env",
                "POSTGRES_DB=market_data_test",
                "--publish",
                "127.0.0.1::5432",
                image,
            ]
        )
        started = True
        wait_for_postgresql(container_name)
        port = mapped_port(container_name)
        database_url = (
            "postgresql+asyncpg://postgres:market-data-test-only"
            f"@127.0.0.1:{port}/market_data_test"
        )
        environment = {
            **os.environ,
            "DATABASE_URL": database_url,
            "DEPLOYMENT_PROFILE": "single-node",
            "MARKET_DATA_MODE": "fake",
            "MARKET_DATA_STORAGE_BACKEND": "postgresql",
            "POSTGRES_TEST_DATABASE_URL": database_url,
            "PYTHONPATH": ".",
        }
        run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=SERVICE_ROOT,
            environment=environment,
        )
        run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=SERVICE_ROOT,
            environment=environment,
        )
        test_result = run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_postgres_market_data.py",
                "-q",
            ],
            cwd=SERVICE_ROOT,
            environment=environment,
        )
        print(test_result.stdout.strip())
        print(
            "PostgreSQL 16 market-data verification passed: concurrent "
            "idempotency, provider revisions, canonical reads, and range plans."
        )
        return 0
    except (MarketDataVerificationError, OSError) as error:
        print(
            f"PostgreSQL market-data verification failed: {error}",
            file=sys.stderr,
        )
        return 1
    finally:
        if started:
            run(["docker", "rm", "--force", container_name], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
