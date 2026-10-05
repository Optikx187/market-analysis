#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
REPORT_SCHEMA_VERSION = 1
OUTPUT_TAIL_LIMIT = 2_000

BACKEND_SERVICES = (
    "data-ingestion",
    "quant-engine",
    "portfolio-engine",
    "notification-gateway",
)
SERVICE_NAMES = (*BACKEND_SERVICES, "frontend", "management", "compose", "docs")
CORE_JOBS = (
    "repository",
    "management",
    "data-ingestion",
    "quant-engine",
    "portfolio-engine",
    "notification-gateway",
    "frontend",
    "compose",
)
FULL_JOBS = (
    *CORE_JOBS,
    "docs",
    "image-data-ingestion",
    "image-quant-engine",
    "image-portfolio-engine",
    "image-notification-gateway",
    "image-frontend",
)

SAFE_ENVIRONMENT = {
    "ALPACA_DATA_URL": "https://alpaca-data.invalid/v2",
    "ALPACA_TRADING_URL": "https://alpaca-paper.invalid",
    "AUTH_ENABLED": "false",
    "BINANCE_REST_URL": "https://binance.invalid/api/v3",
    "CI": "true",
    "CREDENTIAL_ENCRYPTION_KEYS": (
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    ),
    "DEPLOYMENT_PROFILE": "sandbox",
    "INTERNAL_SERVICE_TOKEN": "verification-internal-token",
    "JWT_SECRET": "verification-only-jwt-secret-at-least-32-characters",
    "LIVE_BROKER_BASE_URL": "https://broker.invalid",
    "LIVE_TRADING_ENABLED": "false",
    "MARKET_DATA_MODE": "fake",
    "NOTIFICATION_MODE": "fake",
    "PYTHONDONTWRITEBYTECODE": "1",
    "SCAN_ENABLED": "false",
    "SETTINGS_OPERATOR_TOKEN": "verification-settings-token",
    "TELEGRAM_API_BASE_URL": "https://telegram.invalid",
    "TWILIO_API_BASE_URL": "https://twilio.invalid",
    "YAHOO_CHART_URL": "https://yahoo.invalid/chart",
}


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    cwd: str = "."
    environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Job:
    name: str
    description: str
    commands: tuple[Command, ...]


def command(*argv: str, cwd: str = ".", environment: dict[str, str] | None = None) -> Command:
    return Command(
        argv=tuple(argv),
        cwd=cwd,
        environment=tuple(sorted((environment or {}).items())),
    )


def job_catalog(base_ref: str) -> dict[str, Job]:
    merge_base = git_output("merge-base", base_ref, "HEAD")
    pytest_environment = {"PYTHONPATH": "."}
    return {
        "repository": Job(
            "repository",
            "Validate committed and working-tree whitespace.",
            (
                command("git", "diff", "--check", f"{merge_base}...HEAD"),
                command("git", "diff", "--check"),
                command("git", "diff", "--cached", "--check"),
            ),
        ),
        "management": Job(
            "management",
            "Run management and verification-tool unit tests.",
            (
                command(
                    PYTHON,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "scripts/tests",
                    "-p",
                    "test_*.py",
                ),
            ),
        ),
        "data-ingestion": Job(
            "data-ingestion",
            "Run data-ingestion tests with reserved provider boundaries.",
            (
                command(
                    PYTHON,
                    "-m",
                    "pytest",
                    "tests",
                    "-q",
                    cwd="services/data-ingestion",
                    environment={
                        **pytest_environment,
                        "DEPLOYMENT_PROFILE": "single-node",
                        "MARKET_DATA_MODE": "live",
                    },
                ),
            ),
        ),
        "quant-engine": Job(
            "quant-engine",
            "Run quant-engine tests.",
            (
                command(
                    PYTHON,
                    "-m",
                    "pytest",
                    "tests",
                    "-q",
                    cwd="services/quant-engine",
                    environment=pytest_environment,
                ),
            ),
        ),
        "portfolio-engine": Job(
            "portfolio-engine",
            "Run portfolio, risk, paper-order, and guarded-live tests.",
            (
                command(
                    PYTHON,
                    "-m",
                    "pytest",
                    "tests",
                    "-q",
                    cwd="services/portfolio-engine",
                    environment=pytest_environment,
                ),
            ),
        ),
        "notification-gateway": Job(
            "notification-gateway",
            "Run notification tests in fake-delivery mode.",
            (
                command(
                    PYTHON,
                    "-m",
                    "pytest",
                    "test_credentials.py",
                    "-q",
                    cwd="services/notification-gateway",
                ),
            ),
        ),
        "frontend": Job(
            "frontend",
            "Typecheck and build the frontend.",
            (
                command("npx", "tsc", "--noEmit", cwd="services/frontend"),
                command("npm", "run", "build", cwd="services/frontend"),
            ),
        ),
        "compose": Job(
            "compose",
            "Render the base and every portable Compose profile.",
            (
                command("docker", "compose", "config", "--quiet"),
                command(
                    "docker",
                    "compose",
                    "-f",
                    "docker-compose.yml",
                    "-f",
                    "deploy/compose.sandbox.yml",
                    "config",
                    "--quiet",
                ),
                command(
                    "docker",
                    "compose",
                    "-f",
                    "docker-compose.yml",
                    "-f",
                    "deploy/compose.single-node.yml",
                    "config",
                    "--quiet",
                ),
                command(
                    "docker",
                    "compose",
                    "-f",
                    "docker-compose.yml",
                    "-f",
                    "deploy/compose.multi-worker.yml",
                    "--profile",
                    "multi-worker",
                    "config",
                    "--quiet",
                ),
            ),
        ),
        "docs": Job(
            "docs",
            "Validate repository-local documentation links.",
            (command(PYTHON, "scripts/check_docs.py"),),
        ),
        **{
            f"image-{service}": Job(
                f"image-{service}",
                f"Build the {service} container image.",
                (
                    command(
                        "docker",
                        "build",
                        "--quiet",
                        f"services/{service}",
                    ),
                ),
            )
            for service in (*BACKEND_SERVICES, "frontend")
        },
    }


def git_output(*arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(arguments)} failed")
    return result.stdout.strip()


def changed_paths(base_ref: str) -> list[str]:
    merge_base = git_output("merge-base", base_ref, "HEAD")
    path_sets = [
        git_output("diff", "--name-only", "--diff-filter=ACMRT", f"{merge_base}...HEAD"),
        git_output("diff", "--name-only", "--diff-filter=ACMRT"),
        git_output("diff", "--cached", "--name-only", "--diff-filter=ACMRT"),
        git_output("ls-files", "--others", "--exclude-standard"),
    ]
    return sorted({path for output in path_sets for path in output.splitlines() if path})


def standard_service_jobs() -> set[str]:
    return {
        "data-ingestion",
        "quant-engine",
        "portfolio-engine",
        "notification-gateway",
        "frontend",
    }


def jobs_for_path(path: str) -> set[str]:
    jobs = {"repository"}
    parts = Path(path).parts
    filename = Path(path).name

    if path == "README.md" or path.startswith(("docs/", "deploy/README", ".agents/")):
        return jobs | {"docs"}
    if path in {"manage", "manage.ps1", "verify-changes", "verify-changes.ps1"}:
        return jobs | {"management"}
    if path == "scripts/check_docs.py":
        return jobs | {"management", "docs"}
    if path == "scripts/verify_changes.py":
        return jobs | set(CORE_JOBS)
    if path == "scripts/ci_sandbox_smoke.sh":
        return jobs | {"management", "compose"}
    if path.startswith("scripts/"):
        return jobs | {"management"}
    if path == ".env.example":
        return jobs | {"management", "compose"}
    if path.startswith(("docker-compose", "deploy/compose.")):
        return jobs | {"compose"}
    if path.startswith((".github/", ".gitlab/")):
        return jobs | set(CORE_JOBS)
    if path.startswith(("shared/", "schemas/", "contracts/")):
        return jobs | standard_service_jobs() | {"compose"}
    if "migrations" in parts or filename in {"alembic.ini", "migration.sql"}:
        return jobs | set(BACKEND_SERVICES) | {"compose"}
    if path.startswith("services/frontend/"):
        jobs.add("frontend")
        if filename in {"Dockerfile", ".dockerignore", "package.json", "package-lock.json"}:
            jobs.add("image-frontend")
        return jobs
    for service in BACKEND_SERVICES:
        prefix = f"services/{service}/"
        if path.startswith(prefix):
            jobs.add(service)
            if filename in {"Dockerfile", ".dockerignore", "requirements.txt"}:
                jobs.add(f"image-{service}")
            return jobs

    return jobs | set(CORE_JOBS)


def classify_paths(paths: Iterable[str]) -> list[str]:
    selected: set[str] = set()
    for path in paths:
        selected.update(jobs_for_path(path))
    return ordered_jobs(selected)


def ci_plan(selected_jobs: Iterable[str]) -> dict[str, object]:
    selected = set(selected_jobs)
    return {
        "backend_services": [
            service for service in BACKEND_SERVICES if service in selected
        ],
        "image_services": [
            service
            for service in (*BACKEND_SERVICES, "frontend")
            if f"image-{service}" in selected
        ],
        "run_frontend": "frontend" in selected,
        "run_management": "management" in selected,
        "run_compose": "compose" in selected,
        "run_docs": "docs" in selected,
        "high_risk": "compose" in selected
        or any(name.startswith("image-") for name in selected),
    }


def ordered_jobs(names: Iterable[str]) -> list[str]:
    requested = set(names)
    return [name for name in FULL_JOBS if name in requested]


def service_jobs(service: str) -> list[str]:
    if service not in SERVICE_NAMES:
        raise ValueError(f"unknown service '{service}'")
    selected = {"repository", service}
    return ordered_jobs(selected)


def tail(value: str) -> str:
    return value[-OUTPUT_TAIL_LIMIT:]


def execute_jobs(
    jobs: Sequence[Job],
    *,
    dry_run: bool,
) -> tuple[list[dict[str, object]], bool]:
    results: list[dict[str, object]] = []
    overall_success = True
    base_environment = {**os.environ, **SAFE_ENVIRONMENT}

    for job in jobs:
        started = time.monotonic()
        command_results: list[dict[str, object]] = []
        status = "planned" if dry_run else "passed"
        label = "PLANNED" if dry_run else "RUN"
        print(f"[{label}] {job.name}: {job.description}", flush=True)
        for job_command in job.commands:
            argv = list(job_command.argv)
            command_result: dict[str, object] = {
                "argv": argv,
                "cwd": job_command.cwd,
                "exit_code": None,
                "duration_seconds": 0.0,
                "stdout_tail": "",
                "stderr_tail": "",
            }
            command_results.append(command_result)
            if dry_run:
                continue
            command_started = time.monotonic()
            environment = {
                **base_environment,
                **dict(job_command.environment),
            }
            try:
                result = subprocess.run(
                    argv,
                    cwd=ROOT / job_command.cwd,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                command_result.update(
                    {
                        "exit_code": result.returncode,
                        "duration_seconds": round(
                            time.monotonic() - command_started, 3
                        ),
                        "stdout_tail": tail(result.stdout),
                        "stderr_tail": tail(result.stderr),
                    }
                )
            except OSError as error:
                command_result.update(
                    {
                        "exit_code": 127,
                        "duration_seconds": round(
                            time.monotonic() - command_started, 3
                        ),
                        "stderr_tail": str(error),
                    }
                )
            if command_result["exit_code"] != 0:
                status = "failed"
                overall_success = False
                print(
                    f"[FAILED] {job.name}: {' '.join(argv)}",
                    file=sys.stderr,
                )
                break
        results.append(
            {
                "name": job.name,
                "description": job.description,
                "status": status,
                "duration_seconds": round(time.monotonic() - started, 3),
                "commands": command_results,
            }
        )
        if status == "passed":
            print(
                f"[PASSED] {job.name} ({time.monotonic() - started:.1f}s)",
                flush=True,
            )
        if status == "failed":
            break

    return results, overall_success


def write_json(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def write_junit(path: Path, report: dict[str, object]) -> None:
    jobs = report["jobs"]
    assert isinstance(jobs, list)
    failures = sum(job["status"] == "failed" for job in jobs)
    skipped = sum(job["status"] == "planned" for job in jobs)
    suite = ET.Element(
        "testsuite",
        {
            "name": "changed-service-verification",
            "tests": str(len(jobs)),
            "failures": str(failures),
            "errors": "0",
            "skipped": str(skipped),
            "time": str(report["duration_seconds"]),
        },
    )
    for job in jobs:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "verification",
                "name": str(job["name"]),
                "time": str(job["duration_seconds"]),
            },
        )
        if job["status"] == "planned":
            ET.SubElement(case, "skipped", {"message": "dry run"})
        elif job["status"] == "failed":
            failure = ET.SubElement(case, "failure", {"message": "command failed"})
            commands = job["commands"]
            assert isinstance(commands, list)
            failed_command = next(
                (
                    item
                    for item in commands
                    if isinstance(item["exit_code"], int) and item["exit_code"] != 0
                ),
                None,
            )
            if failed_command:
                failure.text = str(failed_command["stderr_tail"])
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Run the smallest safe deterministic verification tier."
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--base-ref",
        default="origin/master",
        help="Git ref used to classify committed changes (default: origin/master).",
    )
    common.add_argument(
        "--dry-run",
        action="store_true",
        help="Select and report jobs without executing them.",
    )
    common.add_argument("--json", type=Path, help="Write a JSON summary.")
    common.add_argument("--junit", type=Path, help="Write a JUnit XML summary.")
    subparsers = argument_parser.add_subparsers(dest="mode", required=True)
    subparsers.add_parser(
        "changed",
        parents=[common],
        help="Classify committed and working-tree changes.",
    )
    service_parser = subparsers.add_parser(
        "service",
        parents=[common],
        help="Verify one named service.",
    )
    service_parser.add_argument("service", choices=SERVICE_NAMES)
    subparsers.add_parser(
        "full",
        parents=[common],
        help="Run every verification job.",
    )
    return argument_parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    paths: list[str] = []
    if args.mode == "changed":
        paths = changed_paths(args.base_ref)
        selected_names = classify_paths(paths)
    elif args.mode == "service":
        selected_names = service_jobs(args.service)
    else:
        selected_names = list(FULL_JOBS)

    catalog = job_catalog(args.base_ref)
    selected_jobs = [catalog[name] for name in selected_names]
    print(f"Mode: {args.mode}")
    if paths:
        print(f"Changed paths: {len(paths)}")
    print(f"Selected jobs: {', '.join(selected_names) if selected_names else 'none'}")
    job_results, success = execute_jobs(selected_jobs, dry_run=args.dry_run)
    finished_at = datetime.now(timezone.utc)
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": args.mode,
        "base_ref": args.base_ref,
        "dry_run": args.dry_run,
        "changed_paths": paths,
        "selected_jobs": selected_names,
        "ci_plan": ci_plan(selected_names),
        "success": success,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "jobs": job_results,
    }
    if args.json:
        write_json(args.json, report)
    if args.junit:
        write_junit(args.junit, report)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
