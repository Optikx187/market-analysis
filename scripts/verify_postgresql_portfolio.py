#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "portfolio-engine"
IMAGE = os.getenv("POSTGRES_PORTFOLIO_IMAGE", "postgres:16-alpine")


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
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(arguments)} failed with exit code {result.returncode}"
        )
    return result


def available_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def main() -> int:
    container = f"market-analysis-pg-portfolio-{uuid.uuid4().hex[:12]}"
    port = available_port()
    database_url = (
        f"postgresql+asyncpg://postgres:portfolio-test@127.0.0.1:{port}/portfolio"
    )
    environment = {
        **os.environ,
        "DATABASE_URL": database_url,
        "POSTGRES_PORTFOLIO_TEST_DATABASE_URL": database_url,
        "PORTFOLIO_STORAGE_BACKEND": "postgresql",
        "PORTFOLIO_TENANT_ID": "00000000-0000-7000-8000-000000000001",
        "DEPLOYMENT_PROFILE": "sandbox",
        "LIVE_TRADING_ENABLED": "false",
        "LIVE_BROKER_BASE_URL": "https://broker.invalid",
        "AUTH_ENABLED": "false",
        "PYTHONPATH": ".",
    }
    started = False
    try:
        run([
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--env",
            "POSTGRES_PASSWORD=portfolio-test",
            "--env",
            "POSTGRES_DB=portfolio",
            "--publish",
            f"127.0.0.1:{port}:5432",
            IMAGE,
        ])
        started = True
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            ready = run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_isready",
                    "--username",
                    "postgres",
                    "--dbname",
                    "portfolio",
                ],
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("PostgreSQL did not become ready")

        run(["alembic", "upgrade", "head"], cwd=SERVICE, environment=environment)
        run(["alembic", "upgrade", "head"], cwd=SERVICE, environment=environment)
        run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_postgres_portfolio.py",
                "-q",
            ],
            cwd=SERVICE,
            environment=environment,
        )
        print(
            "PostgreSQL 16 portfolio verification passed: exact decimals, "
            "UUIDv7 identities, concurrency, audit immutability, and user isolation."
        )
        return 0
    except (OSError, RuntimeError) as error:
        print(f"PostgreSQL portfolio verification failed: {error}", file=sys.stderr)
        return 1
    finally:
        if started:
            run(["docker", "rm", "--force", container], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
