#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "postgresql" / "schema.sql"
VERIFY_PATH = ROOT / "contracts" / "postgresql" / "verify.sql"
DEFAULT_IMAGE = "postgres:16-alpine"
EXPECTED_PLAN_INDEXES = (
    "idx_candles_instrument_interval_time",
    "idx_paper_orders_open",
    "idx_delivery_jobs_due",
    "idx_audit_events_tenant_time",
)


class ContractVerificationError(RuntimeError):
    pass


def run(
    arguments: list[str],
    *,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        raise ContractVerificationError(
            f"{' '.join(arguments[:3])} failed with exit code "
            f"{result.returncode}\n{output}"
        )
    return result


def psql(container_name: str, sql: str) -> str:
    result = run(
        [
            "docker",
            "exec",
            "-i",
            container_name,
            "psql",
            "-X",
            "--set",
            "ON_ERROR_STOP=1",
            "--username",
            "postgres",
            "--dbname",
            "contract",
        ],
        input_text=sql,
    )
    return f"{result.stdout}\n{result.stderr}"


def wait_for_postgresql(container_name: str, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_output = ""
    while time.monotonic() < deadline:
        result = run(
            [
                "docker",
                "exec",
                container_name,
                "psql",
                "-X",
                "--tuples-only",
                "--no-align",
                "--username",
                "postgres",
                "--dbname",
                "contract",
                "--command",
                "SELECT 1",
            ],
            check=False,
        )
        last_output = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode == 0 and result.stdout.strip() == "1":
            return
        time.sleep(1)
    raise ContractVerificationError(
        f"PostgreSQL did not become ready within {timeout_seconds:.0f}s: "
        f"{last_output}"
    )


def verify_server_major(container_name: str) -> None:
    result = run(
        [
            "docker",
            "exec",
            container_name,
            "psql",
            "-X",
            "--tuples-only",
            "--no-align",
            "--username",
            "postgres",
            "--dbname",
            "contract",
            "--command",
            "SHOW server_version_num",
        ]
    )
    version_number = result.stdout.strip()
    if not version_number.startswith("16"):
        raise ContractVerificationError(
            f"expected PostgreSQL 16, received server_version_num={version_number!r}"
        )


def verify_output(output: str) -> None:
    if "CONTRACT VERIFIED" not in output:
        raise ContractVerificationError("contract completion marker is missing")
    missing_indexes = [
        index_name for index_name in EXPECTED_PLAN_INDEXES if index_name not in output
    ]
    if missing_indexes:
        raise ContractVerificationError(
            "query plans did not use expected indexes: " + ", ".join(missing_indexes)
        )


def main() -> int:
    image = os.environ.get("POSTGRES_CONTRACT_IMAGE", DEFAULT_IMAGE)
    container_name = f"market-analysis-pg-contract-{uuid.uuid4().hex[:12]}"
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    verification_sql = VERIFY_PATH.read_text(encoding="utf-8")

    run(["docker", "version"])
    started = False
    try:
        run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                container_name,
                "--env",
                "POSTGRES_PASSWORD=contract-only",
                "--env",
                "POSTGRES_DB=contract",
                image,
            ]
        )
        started = True
        wait_for_postgresql(container_name)
        verify_server_major(container_name)
        psql(container_name, schema_sql)
        output = psql(container_name, verification_sql)
        verify_output(output)
        print(
            "PostgreSQL 16 contract verified: exact decimals, UTC, uniqueness, "
            "tenancy, and 4 index plans."
        )
        return 0
    except (ContractVerificationError, OSError) as error:
        print(f"PostgreSQL contract verification failed: {error}", file=sys.stderr)
        return 1
    finally:
        if started:
            run(["docker", "rm", "--force", container_name], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
