#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POSTGRES_IMAGE = "postgres:16-alpine"


class SharedMigrationVerificationError(RuntimeError):
    pass


def run(
    arguments: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        output = "\n".join(
            part.strip()
            for part in (result.stdout, result.stderr)
            if part.strip()
        )
        raise SharedMigrationVerificationError(
            f"{' '.join(arguments[:4])} failed with exit code "
            f"{result.returncode}\n{output}"
        )
    return result


def wait_for_postgresql(
    container_name: str,
    timeout_seconds: float = 60.0,
) -> None:
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
                "shared_migration_test",
            ],
            check=False,
        )
        last_output = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode == 0:
            return
        time.sleep(1)
    raise SharedMigrationVerificationError(
        f"PostgreSQL did not become ready within {timeout_seconds:.0f}s: "
        f"{last_output}"
    )


def container_command(
    image: str,
    environment: dict[str, str],
    *command: str,
) -> list[str]:
    arguments = [
        "docker",
        "run",
        "--rm",
        "--network",
        f"container:{CONTAINER_NAME}",
    ]
    for key, value in environment.items():
        arguments.extend(["--env", f"{key}={value}"])
    arguments.append(image)
    arguments.extend(command)
    return arguments


TOKEN = uuid.uuid4().hex[:12]
CONTAINER_NAME = f"market-analysis-pg-shared-{TOKEN}"
IMAGES = {
    "data": f"market-analysis-data-shared-test:{TOKEN}",
    "research": f"market-analysis-research-shared-test:{TOKEN}",
    "portfolio": f"market-analysis-portfolio-shared-test:{TOKEN}",
}


def main() -> int:
    started = False
    built_images: list[str] = []
    password = "shared-migration-test-only"
    async_database_url = (
        "postgresql+asyncpg://postgres:"
        f"{password}@localhost:5432/shared_migration_test"
    )
    sync_database_url = (
        "postgresql+psycopg://postgres:"
        f"{password}@localhost:5432/shared_migration_test"
    )
    service_environments = {
        "data": {
            "DATABASE_URL": async_database_url,
            "MARKET_DATA_STORAGE_BACKEND": "postgresql",
            "MARKET_DATA_MODE": "fake",
            "DEPLOYMENT_PROFILE": "single-node",
        },
        "research": {
            "RESEARCH_DATABASE_URL": sync_database_url,
            "RESEARCH_STORAGE_BACKEND": "postgresql",
            "RESEARCH_TENANT_ID": (
                "00000000-0000-7000-8000-000000000001"
            ),
            "RESEARCH_USER_KEY": "default",
        },
        "portfolio": {
            "DATABASE_URL": async_database_url,
            "PORTFOLIO_STORAGE_BACKEND": "postgresql",
            "PORTFOLIO_TENANT_ID": (
                "00000000-0000-7000-8000-000000000001"
            ),
        },
    }
    service_paths = {
        "data": "services/data-ingestion",
        "research": "services/quant-engine",
        "portfolio": "services/portfolio-engine",
    }
    validation_commands = {
        "data": (
            "python",
            "-c",
            "import asyncio; from app.migration_state import validate_schema; "
            "asyncio.run(validate_schema())",
        ),
        "research": (
            "python",
            "-c",
            "from app.config import settings; "
            "from app.migration_state import validate_schema; "
            "validate_schema(settings.research_database_url)",
        ),
        "portfolio": (
            "python",
            "-c",
            "import asyncio; from app.migration_state import validate_schema; "
            "asyncio.run(validate_schema())",
        ),
    }
    try:
        run(["docker", "version"])
        run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                CONTAINER_NAME,
                "--env",
                f"POSTGRES_PASSWORD={password}",
                "--env",
                "POSTGRES_DB=shared_migration_test",
                POSTGRES_IMAGE,
            ]
        )
        started = True
        wait_for_postgresql(CONTAINER_NAME)
        for service, image in IMAGES.items():
            run(
                [
                    "docker",
                    "build",
                    "--quiet",
                    "--tag",
                    image,
                    service_paths[service],
                ]
            )
            built_images.append(image)
        for _attempt in range(2):
            for service in ("data", "research", "portfolio"):
                run(
                    container_command(
                        IMAGES[service],
                        service_environments[service],
                        "python",
                        "-m",
                        "alembic",
                        "upgrade",
                        "head",
                    )
                )
        for service in ("data", "research", "portfolio"):
            run(
                container_command(
                    IMAGES[service],
                    service_environments[service],
                    *validation_commands[service],
                )
            )
        revisions = run(
            [
                "docker",
                "exec",
                CONTAINER_NAME,
                "psql",
                "--username",
                "postgres",
                "--dbname",
                "shared_migration_test",
                "--tuples-only",
                "--no-align",
                "--command",
                "SELECT 'data=' || version_num FROM alembic_version "
                "UNION ALL SELECT 'research=' || version_num "
                "FROM alembic_version_research "
                "UNION ALL SELECT 'portfolio=' || version_num "
                "FROM alembic_version_portfolio ORDER BY 1",
            ]
        ).stdout.strip().splitlines()
        expected = [
            "data=0003_data",
            "portfolio=0002_portfolio",
            "research=0002_research",
        ]
        if revisions != expected:
            raise SharedMigrationVerificationError(
                f"Unexpected shared migration revisions: {revisions}"
            )
        print(
            "Shared PostgreSQL migration verification passed: independent "
            "data, research, and portfolio revision tables are current and "
            "repeatable."
        )
        return 0
    except (OSError, SharedMigrationVerificationError) as error:
        print(
            f"Shared PostgreSQL migration verification failed: {error}",
            file=sys.stderr,
        )
        return 1
    finally:
        if started:
            run(["docker", "rm", "--force", CONTAINER_NAME], check=False)
        for image in built_images:
            run(["docker", "image", "rm", "--force", image], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
