#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POSTGRES_IMAGE = "postgres:16-alpine"


class VerificationError(RuntimeError):
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
        raise VerificationError(
            f"{' '.join(arguments[:5])} failed ({result.returncode})\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result


def wait_for_postgresql(name: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = run(
            [
                "docker",
                "exec",
                name,
                "pg_isready",
                "-U",
                "postgres",
                "-d",
                "operations_test",
            ],
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise VerificationError("PostgreSQL did not become ready")


def service_run(
    image: str,
    environment: dict[str, str],
    *command: str,
) -> None:
    arguments = [
        "docker",
        "run",
        "--rm",
        "--network",
        f"container:{CONTAINER}",
    ]
    for key, value in environment.items():
        arguments.extend(["--env", f"{key}={value}"])
    arguments.extend([image, *command])
    run(arguments)


def operations(
    command: list[str],
    *,
    mounts: list[tuple[Path, str, bool]] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    arguments = [
        "docker",
        "run",
        "--rm",
        "--network",
        f"container:{CONTAINER}",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        f"POSTGRES_OPERATIONS_DATABASE_URL={SYNC_URL}",
    ]
    for source, target, read_only in mounts or []:
        suffix = ":ro" if read_only else ""
        arguments.extend(["--volume", f"{source}:{target}{suffix}"])
    arguments.extend([IMAGES["operations"], *command])
    return run(arguments, check=check)


def scalar(query: str) -> str:
    return run(
        [
            "docker",
            "exec",
            CONTAINER,
            "psql",
            "-U",
            "postgres",
            "-d",
            "operations_test",
            "-tAc",
            query,
        ]
    ).stdout.strip()


TOKEN = uuid.uuid4().hex[:12]
CONTAINER = f"market-analysis-pg-operations-{TOKEN}"
IMAGES = {
    "data": f"market-analysis-data-operations:{TOKEN}",
    "research": f"market-analysis-research-operations:{TOKEN}",
    "portfolio": f"market-analysis-portfolio-operations:{TOKEN}",
    "operations": f"market-analysis-postgresql-operations:{TOKEN}",
}
PASSWORD = "operations-test-only"
ASYNC_URL = (
    f"postgresql+asyncpg://postgres:{PASSWORD}@localhost:5432/operations_test"
)
SYNC_URL = (
    f"postgresql://postgres:{PASSWORD}@localhost:5432/operations_test"
)


def main() -> int:
    workspace = Path(tempfile.mkdtemp(prefix="postgres-operations-"))
    backup = workspace / "backup"
    corrupt = workspace / "corrupt"
    unsafe = workspace / "unsafe"
    started = False
    built: list[str] = []
    try:
        run(["docker", "version"])
        run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                CONTAINER,
                "--env",
                f"POSTGRES_PASSWORD={PASSWORD}",
                "--env",
                "POSTGRES_DB=operations_test",
                POSTGRES_IMAGE,
            ]
        )
        started = True
        wait_for_postgresql(CONTAINER)
        paths = {
            "data": "services/data-ingestion",
            "research": "services/quant-engine",
            "portfolio": "services/portfolio-engine",
            "operations": "tools/postgresql-operations",
        }
        for name, image in IMAGES.items():
            run(["docker", "build", "--quiet", "--tag", image, paths[name]])
            built.append(image)
        environments = {
            "data": {
                "DATABASE_URL": ASYNC_URL,
                "MARKET_DATA_STORAGE_BACKEND": "postgresql",
            },
            "research": {
                "RESEARCH_DATABASE_URL": SYNC_URL.replace(
                    "postgresql://", "postgresql+psycopg://"
                ),
                "RESEARCH_STORAGE_BACKEND": "postgresql",
            },
            "portfolio": {
                "DATABASE_URL": ASYNC_URL,
                "PORTFOLIO_STORAGE_BACKEND": "postgresql",
            },
        }
        for name in ("data", "research", "portfolio"):
            service_run(
                IMAGES[name],
                environments[name],
                "python",
                "-m",
                "alembic",
                "upgrade",
                "head",
            )
        seed = """
        INSERT INTO market_data.providers(provider_key, display_name)
        VALUES ('fixture', 'Fixture');
        INSERT INTO market_data.venues(id, venue_key, display_name)
        VALUES ('10000000-0000-7000-8000-000000000001', 'fixture', 'Fixture');
        INSERT INTO market_data.instruments(
            id, instrument_key, display_name, asset_class,
            base_asset, quote_asset, venue_id
        ) VALUES (
            '20000000-0000-7000-8000-000000000001',
            'fixture:BTC-USD', 'Bitcoin', 'crypto', 'BTC', 'USD',
            '10000000-0000-7000-8000-000000000001'
        );
        INSERT INTO market_data.candles(
            provider_id, instrument_id, bar_interval, bucket_start,
            open_price, high_price, low_price, close_price, volume,
            provider_observed_at, content_checksum
        )
        SELECT 1, '20000000-0000-7000-8000-000000000001', interval_name,
               bucket, 100, 110, 90, 105, 1, bucket, checksum
        FROM (VALUES
            ('1m', '2020-01-01T00:00:00Z'::timestamptz, 'old-1m'),
            ('1m', '2029-12-01T00:00:00Z'::timestamptz, 'recent-1m'),
            ('1h', '2020-01-01T00:00:00Z'::timestamptz, 'old-1h'),
            ('1d', '2000-01-01T00:00:00Z'::timestamptz, 'old-1d')
        ) AS fixture(interval_name, bucket, checksum);
        INSERT INTO operations.import_runs(
            id, tenant_id, source_fingerprint, source_fingerprint_sha256,
            mode, status, operator_id
        ) VALUES (
            '30000000-0000-7000-8000-000000000001',
            '00000000-0000-7000-8000-000000000001',
            '{}', 'fixture', 'apply', 'reconciled', 'fixture'
        );
        """
        run(
            [
                "docker",
                "exec",
                CONTAINER,
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "postgres",
                "-d",
                "operations_test",
                "-c",
                seed,
            ]
        )
        backup.mkdir()
        operations(
            ["backup", "--directory", "/backup"],
            mounts=[(backup, "/backup", False)],
        )
        operations(
            ["verify", "--directory", "/backup"],
            mounts=[(backup, "/backup", True)],
        )
        operations(
            ["restore-drill", "--directory", "/backup"],
            mounts=[(backup, "/backup", True)],
        )
        shutil.copytree(backup, corrupt)
        with (corrupt / "database.dump").open("ab") as stream:
            stream.write(b"corruption")
        if operations(
            ["verify", "--directory", "/backup"],
            mounts=[(corrupt, "/backup", True)],
            check=False,
        ).returncode == 0:
            raise VerificationError("Corrupt logical backup was accepted")
        shutil.copytree(backup, unsafe)
        unsafe_manifest = json.loads((unsafe / "manifest.json").read_text())
        unsafe_manifest["dump_file"] = "../database.dump"
        (unsafe / "manifest.json").write_text(
            json.dumps(unsafe_manifest, indent=2) + "\n"
        )
        if operations(
            ["verify", "--directory", "/backup"],
            mounts=[(unsafe, "/backup", True)],
            check=False,
        ).returncode == 0:
            raise VerificationError("Unsafe backup manifest path was accepted")
        run(
            [
                "docker",
                "exec",
                CONTAINER,
                "psql",
                "-U",
                "postgres",
                "-d",
                "operations_test",
                "-c",
                """
                INSERT INTO market_data.candles(
                    provider_id, instrument_id, bar_interval, bucket_start,
                    open_price, high_price, low_price, close_price, volume,
                    provider_observed_at, content_checksum
                ) VALUES (
                    1, '20000000-0000-7000-8000-000000000001', '1d',
                    '2029-12-02T00:00:00Z', 100, 110, 90, 105, 1,
                    '2029-12-02T00:00:00Z', 'post-backup'
                )
                """,
            ]
        )
        stale = operations(
            [
                "retention",
                "--as-of",
                "2030-01-01T00:00:00+00:00",
                "--apply",
                "--archive-directory",
                "/backup",
            ],
            mounts=[(backup, "/backup", True)],
            check=False,
        )
        if stale.returncode == 0:
            raise VerificationError("Stale backup allowed retention deletion")
        run(
            [
                "docker",
                "exec",
                CONTAINER,
                "psql",
                "-U",
                "postgres",
                "-d",
                "operations_test",
                "-c",
                (
                    "DELETE FROM market_data.candles "
                    "WHERE content_checksum = 'post-backup'"
                ),
            ]
        )
        dry_run = json.loads(
            operations(
                ["retention", "--as-of", "2030-01-01T00:00:00+00:00"]
            ).stdout
        )
        if [item["eligible_rows"] for item in dry_run["policies"]] != [1, 1]:
            raise VerificationError(f"Unexpected retention boundary: {dry_run}")
        operations(
            [
                "retention",
                "--as-of",
                "2030-01-01T00:00:00+00:00",
                "--apply",
                "--archive-directory",
                "/backup",
                "--batch-size",
                "1",
            ],
            mounts=[(backup, "/backup", True)],
        )
        if scalar("SELECT count(*) FROM market_data.candles") != "2":
            raise VerificationError("Eligible candles were not deleted")
        if scalar(
            "SELECT count(*) FROM market_data.candles "
            "WHERE bar_interval = '1d'"
        ) != "1":
            raise VerificationError("Indefinite daily candle was deleted")
        if scalar("SELECT count(*) FROM operations.import_runs") != "1":
            raise VerificationError("Import audit evidence was deleted")
        operations(["maintain", "--table", "market_data.candles"])
        status = json.loads(operations(["status"]).stdout)
        if "database_bytes" not in status or not status["tables"]:
            raise VerificationError("Growth status omitted required metrics")
        print(
            "PostgreSQL operations verification passed: logical backup, "
            "checksum rejection, isolated restore, bounded retention, "
            "evidence preservation, maintenance, and growth status."
        )
        return 0
    except (OSError, VerificationError, json.JSONDecodeError) as error:
        print(f"PostgreSQL operations verification failed: {error}", file=sys.stderr)
        return 1
    finally:
        if started:
            run(["docker", "rm", "--force", CONTAINER], check=False)
        for image in built:
            run(["docker", "image", "rm", "--force", image], check=False)
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
