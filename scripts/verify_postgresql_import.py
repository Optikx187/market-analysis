#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SCRIPT = (
    ROOT / "tools" / "postgresql-import" / "tests" / "create_fixture.py"
)
POSTGRES_IMAGE = os.getenv("POSTGRES_IMPORT_IMAGE", "postgres:16-alpine")


class VerificationError(RuntimeError):
    pass


def run(
    arguments: list[str],
    *,
    cwd: Path = ROOT,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        cwd=cwd,
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
        raise VerificationError(
            f"{' '.join(arguments[:5])} failed with "
            f"exit code {result.returncode}\n{output}"
        )
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_postgresql(container: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = run(
            [
                "docker",
                "exec",
                container,
                "pg_isready",
                "-U",
                "postgres",
                "-d",
                "import_test",
            ],
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise VerificationError("PostgreSQL did not become ready within 60 seconds")


def docker_migration(
    image: str,
    network: str,
    environment: dict[str, str],
) -> None:
    arguments = ["docker", "run", "--rm", "--network", network]
    for key, value in environment.items():
        arguments.extend(("--env", f"{key}={value}"))
    arguments.extend((image, "alembic", "upgrade", "head"))
    run(arguments)


def migrate_target(
    database: str,
    network: str,
    images: dict[str, str],
) -> None:
    async_url = (
        f"postgresql+asyncpg://postgres:import-test@postgresql/{database}"
    )
    sync_url = (
        f"postgresql+psycopg://postgres:import-test@postgresql/{database}"
    )
    docker_migration(
        images["data"],
        network,
        {
            "DATABASE_URL": async_url,
            "MARKET_DATA_STORAGE_BACKEND": "postgresql",
        },
    )
    docker_migration(
        images["research"],
        network,
        {
            "RESEARCH_DATABASE_URL": sync_url,
            "RESEARCH_STORAGE_BACKEND": "postgresql",
        },
    )
    docker_migration(
        images["portfolio"],
        network,
        {
            "DATABASE_URL": async_url,
            "PORTFOLIO_STORAGE_BACKEND": "postgresql",
            "LIVE_TRADING_ENABLED": "false",
            "CREDENTIAL_ENCRYPTION_KEYS": "fixture-only",
        },
    )


def migrate_sqlite(
    fixture: Path,
    images: dict[str, str],
) -> None:
    commands = (
        (
            images["data"],
            fixture / "market",
            {
                "DATABASE_URL": (
                    "sqlite+aiosqlite:////fixture/data_ingestion.db"
                ),
                "MARKET_DATA_STORAGE_BACKEND": "sqlite-transition",
            },
        ),
        (
            images["research"],
            fixture / "research",
            {
                "RESEARCH_DATABASE_URL": "sqlite:////fixture/backtests.db",
                "RESEARCH_STORAGE_BACKEND": "sqlite-transition",
                "BACKTEST_DATABASE_PATH": "/fixture/backtests.db",
            },
        ),
        (
            images["portfolio"],
            fixture / "portfolio",
            {
                "DATABASE_URL": "sqlite+aiosqlite:////fixture/portfolio.db",
                "PORTFOLIO_STORAGE_BACKEND": "sqlite-transition",
                "CREDENTIAL_ENCRYPTION_KEYS": "fixture-only",
            },
        ),
    )
    for image, directory, environment in commands:
        arguments = [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{directory}:/fixture",
        ]
        for key, value in environment.items():
            arguments.extend(("--env", f"{key}={value}"))
        arguments.extend((image, "alembic", "upgrade", "head"))
        run(arguments)


def runner(
    *,
    image: str,
    network: str,
    fixture: Path,
    database: str,
    extra_arguments: list[str],
    expected_exit: int,
) -> dict[str, object]:
    database_url = (
        f"postgresql://postgres:import-test@postgresql/{database}"
    )
    arguments = [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "--env",
        f"POSTGRES_IMPORT_DATABASE_URL={database_url}",
        "--volume",
        f"{fixture / 'market'}:/sources/market:ro",
        "--volume",
        f"{fixture / 'research'}:/sources/research:ro",
        "--volume",
        f"{fixture / 'portfolio'}:/sources/portfolio:ro",
        "--volume",
        f"{fixture / 'target-artifacts'}:/target/research-artifacts",
        "--volume",
        f"{fixture / 'backup'}:/backup:ro",
        image,
        "--market-sqlite",
        "/sources/market/data_ingestion.db",
        "--research-sqlite",
        "/sources/research/backtests.db",
        "--portfolio-sqlite",
        "/sources/portfolio/portfolio.db",
        "--research-source-root",
        "/sources/research",
        "--research-target-artifact-root",
        "/target/research-artifacts",
        "--research-runtime-artifact-root",
        "/app/artifacts",
        "--operator-id",
        "verification",
        *extra_arguments,
    ]
    result = run(arguments, check=False)
    if result.returncode != expected_exit:
        raise VerificationError(
            f"Migration runner returned {result.returncode}, expected "
            f"{expected_exit}\n{result.stdout}\n{result.stderr}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise VerificationError(
            f"Migration runner returned invalid JSON: {result.stdout}"
        ) from error


def psql(container: str, database: str, query: str) -> str:
    return run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            "postgres",
            "-d",
            database,
            "-Atc",
            query,
        ]
    ).stdout.strip()


def assert_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise VerificationError(
            f"{label}: expected {expected!r}, got {actual!r}"
        )


def main() -> int:
    suffix = uuid.uuid4().hex[:12]
    network = f"market-import-{suffix}"
    postgres = f"market-import-pg-{suffix}"
    workspace = ROOT / "artifacts" / f"postgresql-import-{suffix}"
    images = {
        "data": f"market-import-data:{suffix}",
        "research": f"market-import-research:{suffix}",
        "portfolio": f"market-import-portfolio:{suffix}",
        "runner": f"market-import-runner:{suffix}",
    }
    started = False
    try:
        run(["docker", "version"])
        workspace.mkdir(parents=True)
        for directory in (
            workspace / "market",
            workspace / "research" / "artifacts",
            workspace / "portfolio",
            workspace / "target-artifacts",
            workspace / "backup",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        run(
            [
                "docker",
                "build",
                "-t",
                images["data"],
                str(ROOT / "services" / "data-ingestion"),
            ]
        )
        run(
            [
                "docker",
                "build",
                "-t",
                images["research"],
                str(ROOT / "services" / "quant-engine"),
            ]
        )
        run(
            [
                "docker",
                "build",
                "-t",
                images["portfolio"],
                str(ROOT / "services" / "portfolio-engine"),
            ]
        )
        run(
            [
                "docker",
                "build",
                "-t",
                images["runner"],
                str(ROOT / "tools" / "postgresql-import"),
            ]
        )
        migrate_sqlite(workspace, images)
        run(
            [
                "docker",
                "run",
                "--rm",
                "--volume",
                f"{workspace}:/fixture",
                "--volume",
                f"{FIXTURE_SCRIPT}:/create_fixture.py:ro",
                "python:3.12-slim",
                "python",
                "/create_fixture.py",
                "--root",
                "/fixture",
            ]
        )
        run(
            [
                "docker",
                "run",
                "--rm",
                "--volume",
                f"{workspace}:/fixture",
                "python:3.12-slim",
                "chmod",
                "-R",
                "a+rwX",
                "/fixture",
            ]
        )
        sources = (
            workspace / "market" / "data_ingestion.db",
            workspace / "research" / "backtests.db",
            workspace / "portfolio" / "portfolio.db",
        )
        source_hashes = {path: sha256(path) for path in sources}
        (workspace / "backup" / "manifest.json").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "volumes": [
                        {"archive_sha256": character * 64}
                        for character in ("a", "b", "c")
                    ],
                }
            )
        )

        run(["docker", "network", "create", network])
        run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                postgres,
                "--network",
                network,
                "--network-alias",
                "postgresql",
                "--env",
                "POSTGRES_PASSWORD=import-test",
                "--env",
                "POSTGRES_DB=import_test",
                POSTGRES_IMAGE,
            ]
        )
        started = True
        wait_for_postgresql(postgres)
        migrate_target("import_test", network, images)

        decision = runner(
            image=images["runner"],
            network=network,
            fixture=workspace,
            database="import_test",
            extra_arguments=["--dry-run"],
            expected_exit=1,
        )
        assert_equal(decision["blocked"], True, "deferred decision blocked")
        if not decision["operator_decisions"]:
            raise VerificationError("Dry run omitted the price-alert decision")
        assert_equal(
            psql(
                postgres,
                "import_test",
                "SELECT COUNT(*) FROM market_data.candles",
            ),
            "0",
            "dry run domain writes",
        )

        dry_run = runner(
            image=images["runner"],
            network=network,
            fixture=workspace,
            database="import_test",
            extra_arguments=[
                "--dry-run",
                "--accept-deferred",
                "market.price_alerts",
            ],
            expected_exit=0,
        )
        assert_equal(dry_run["reconciled"], True, "clean dry run")
        assert_equal(dry_run["source_unchanged"], True, "dry-run source hash")

        interrupted = runner(
            image=images["runner"],
            network=network,
            fixture=workspace,
            database="import_test",
            extra_arguments=[
                "--apply",
                "--verified-backup-manifest",
                "/backup/manifest.json",
                "--accept-deferred",
                "market.price_alerts",
                "--stop-after-table",
                "portfolio.trades",
            ],
            expected_exit=3,
        )
        run_id = str(interrupted["import_run_id"])
        assert_equal(interrupted["reconciled"], False, "interrupted run")
        resumed = runner(
            image=images["runner"],
            network=network,
            fixture=workspace,
            database="import_test",
            extra_arguments=[
                "--apply",
                "--verified-backup-manifest",
                "/backup/manifest.json",
                "--accept-deferred",
                "market.price_alerts",
                "--resume",
                run_id,
            ],
            expected_exit=0,
        )
        assert_equal(resumed["reconciled"], True, "resumed run")
        repeated = runner(
            image=images["runner"],
            network=network,
            fixture=workspace,
            database="import_test",
            extra_arguments=[
                "--apply",
                "--verified-backup-manifest",
                "/backup/manifest.json",
                "--accept-deferred",
                "market.price_alerts",
            ],
            expected_exit=0,
        )
        assert_equal(repeated["reconciled"], True, "repeated run")
        for name, table in repeated["tables"].items():
            if name == "market.price_alerts":
                continue
            if table["source_rows"] and table["imported_rows"]:
                raise VerificationError(
                    f"Repeated import inserted rows for {name}: {table}"
                )

        assert_equal(
            psql(
                postgres,
                "import_test",
                "SELECT balance || '|' || equity || '|' || total_pnl "
                "FROM portfolio.portfolios",
            ),
            "9876.540000000000|10023.450000000000|23.450000000000",
            "exact portfolio reconciliation",
        )
        assert_equal(
            psql(
                postgres,
                "import_test",
                "SELECT acknowledged || '|' || trading_disabled "
                "FROM portfolio.live_trading_control",
            ),
            "false|true",
            "live execution fail-closed",
        )
        assert_equal(
            psql(
                postgres,
                "import_test",
                "SELECT COUNT(*) FROM operations.import_deferred_records",
            ),
            "1",
            "deferred record idempotency",
        )
        assert_equal(
            psql(
                postgres,
                "import_test",
                "SELECT COUNT(*) FROM research.backtest_artifacts",
            ),
            "1",
            "research artifact reconciliation",
        )
        for path, before in source_hashes.items():
            assert_equal(sha256(path), before, f"source unchanged {path.name}")

        changed_market = workspace / "market" / "changed.db"
        shutil.copy2(sources[0], changed_market)
        connection = sqlite3.connect(changed_market)
        connection.execute(
            "INSERT INTO candles (id, ticker, timestamp, open, high, low, "
            "close, volume, interval) VALUES "
            "(99, 'AAPL', '2026-01-02 00:00:00', 100, 111, 95, 106, "
            "1234.5, '1d')"
        )
        connection.commit()
        connection.close()
        original_market = workspace / "market" / "data_ingestion.db"
        original_backup = workspace / "market" / "original.db"
        original_market.rename(original_backup)
        changed_market.rename(original_market)
        populated_conflict = runner(
            image=images["runner"],
            network=network,
            fixture=workspace,
            database="import_test",
            extra_arguments=[
                "--dry-run",
                "--accept-deferred",
                "market.price_alerts",
            ],
            expected_exit=1,
        )
        if not populated_conflict["operator_decisions"]:
            raise VerificationError(
                "Changed source did not block a populated target"
            )

        run(
            [
                "docker",
                "exec",
                postgres,
                "createdb",
                "-U",
                "postgres",
                "conflict_test",
            ]
        )
        migrate_target("conflict_test", network, images)
        duplicate_conflict = runner(
            image=images["runner"],
            network=network,
            fixture=workspace,
            database="conflict_test",
            extra_arguments=[
                "--dry-run",
                "--accept-deferred",
                "market.price_alerts",
            ],
            expected_exit=1,
        )
        if not any(
            "conflicting duplicate candle" in error
            for error in duplicate_conflict["errors"]
        ):
            raise VerificationError(
                "Duplicate/conflict fixture was not rejected"
            )
        original_market.unlink()
        original_backup.rename(original_market)
        for path, before in source_hashes.items():
            assert_equal(sha256(path), before, f"restored source {path.name}")

        print(
            "PostgreSQL import verification passed: dry-run decisions, "
            "read-only sources, interrupted resume, repeated idempotency, "
            "duplicate conflicts, exact finance, artifacts, and fail-closed "
            "live controls."
        )
        return 0
    except (OSError, VerificationError) as error:
        print(f"PostgreSQL import verification failed: {error}", file=sys.stderr)
        return 1
    finally:
        if started:
            run(["docker", "rm", "--force", postgres], check=False)
        run(["docker", "network", "rm", network], check=False)
        for image in images.values():
            run(["docker", "image", "rm", "--force", image], check=False)
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
