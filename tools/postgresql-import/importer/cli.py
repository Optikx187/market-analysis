from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from importer.common import ReadOnlySQLite, canonical_json, checksum
from importer.market import import_market
from importer.portfolio import import_portfolio
from importer.report import ImportReport
from importer.research import import_research
from importer.target import Target

EXPECTED_REVISIONS = {
    "market": "0003_data",
    "research": "0002_research",
    "portfolio": "0002_portfolio",
}

DOMAIN_TABLES = {
    "market_data": ("instruments", "instrument_symbols", "candles"),
    "research": ("backtest_runs", "backtest_artifacts"),
    "portfolio": (
        "users",
        "credential_secrets",
        "portfolios",
        "trades",
        "trade_executions",
        "trade_journals",
        "paper_orders",
        "paper_order_fills",
        "paper_order_events",
        "equity_snapshots",
        "risk_snapshots",
        "alert_logs",
        "action_items",
        "dashboard_preferences",
        "live_trading_control",
        "live_orders",
        "live_order_fills",
        "live_execution_audit",
    ),
}


def parse_mapping(value: str) -> dict[str, str]:
    if not value:
        return {}
    path = Path(value)
    payload = json.loads(path.read_text() if path.is_file() else value)
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in payload.items()
    ):
        raise ValueError("user mapping must be a JSON string-to-string object")
    return payload


def verified_backup(path_value: str | None) -> Path:
    if not path_value:
        raise ValueError("--verified-backup-manifest is required for --apply")
    path = Path(path_value).resolve()
    payload = json.loads(path.read_text())
    if payload.get("format_version") != 1:
        raise ValueError("backup manifest format_version must be 1")
    volumes = payload.get("volumes")
    if not isinstance(volumes, list) or len(volumes) != 3:
        raise ValueError("backup manifest must describe all three source volumes")
    for volume in volumes:
        if (
            not isinstance(volume, dict)
            or len(str(volume.get("archive_sha256", ""))) != 64
        ):
            raise ValueError("backup manifest contains an invalid archive checksum")
    return path


def source_fingerprint(
    sources: dict[str, ReadOnlySQLite],
) -> tuple[dict[str, object], str]:
    value = {
        name: source.fingerprint()
        for name, source in sorted(sources.items())
    }
    return value, checksum(value)


def run_import(
    args: argparse.Namespace,
    *,
    mode: str,
) -> ImportReport:
    sources = {
        "market": ReadOnlySQLite(Path(args.market_sqlite)),
        "research": ReadOnlySQLite(Path(args.research_sqlite)),
        "portfolio": ReadOnlySQLite(Path(args.portfolio_sqlite)),
    }
    fingerprint, fingerprint_sha256 = source_fingerprint(sources)
    user_map = parse_mapping(args.user_map)
    accepted_deferred = set(args.accept_deferred or [])
    options = {
        "accepted_deferred": sorted(accepted_deferred),
        "user_map": user_map,
        "live_execution_fail_closed": True,
        "automatic_cutover": False,
    }
    report = ImportReport(
        mode=mode,
        source_fingerprint_sha256=fingerprint_sha256,
    )
    target = Target(
        args.database_url,
        tenant_id=args.tenant_id,
        operator_id=args.operator_id,
        mode=mode,
        fingerprint_sha256=fingerprint_sha256,
        fingerprint=fingerprint,
        options=options,
        resume_run_id=args.resume if mode == "apply" else None,
    )
    report.import_run_id = target.run_id
    try:
        for name, source in sources.items():
            revision = source.revision()
            expected = EXPECTED_REVISIONS[name]
            if revision != expected:
                report.errors.append(
                    f"{name} source revision is {revision!r}; "
                    f"run SQLite migrations to {expected} before import"
                )
        if target.mapping_count() == 0:
            populated = [
                f"{schema}.{table}={target.count(schema, table)}"
                for schema, tables in DOMAIN_TABLES.items()
                for table in tables
                if target.count(schema, table) > 0
            ]
            if populated:
                report.decisions.append(
                    "Target is not empty and has no mappings for these "
                    "sources; use a fresh target or follow documented cleanup: "
                    + ", ".join(populated)
                )
        if report.blocked:
            report.source_unchanged = all(
                _source_unchanged(source) for source in sources.values()
            )
            target.finish(status="failed", report=report.as_dict())
            return report
        import_market(
            sources["market"],
            target,
            report,
            accepted_deferred=accepted_deferred,
        )
        import_research(
            sources["research"],
            target,
            report,
            source_artifact_root=Path(args.research_source_root),
            target_artifact_root=Path(args.research_target_artifact_root),
            runtime_artifact_root=args.research_runtime_artifact_root,
            user_map=user_map,
        )
        import_portfolio(
            sources["portfolio"],
            target,
            report,
            user_map=user_map,
            stop_after_table=args.stop_after_table if mode == "apply" else None,
        )
        for name, source in sources.items():
            try:
                source.assert_unchanged()
            except RuntimeError as error:
                report.errors.append(f"{name}: {error}")
        report.source_unchanged = not any(
            "SQLite source changed" in error for error in report.errors
        )
        for table_name, table_report in report.tables.items():
            accounted = (
                table_report.imported_rows
                + table_report.skipped_rows
                + table_report.deferred_rows
                + table_report.conflict_rows
            )
            if accounted != table_report.source_rows:
                report.errors.append(
                    f"{table_name} reconciliation count mismatch: "
                    f"source={table_report.source_rows}, accounted={accounted}"
                )
        report.reconciled = not report.blocked and report.source_unchanged
        target.finish(
            status="reconciled" if report.reconciled else "failed",
            report=report.as_dict(),
        )
        return report
    except InterruptedError as error:
        report.warnings.append(str(error))
        report.source_unchanged = all(
            _source_unchanged(source) for source in sources.values()
        )
        target.finish(status="interrupted", report=report.as_dict())
        return report
    except Exception as error:  # noqa: BLE001 - CLI must persist failure reports
        target.rollback()
        report.errors.append(str(error))
        report.source_unchanged = all(
            _source_unchanged(source) for source in sources.values()
        )
        target.finish(status="failed", report=report.as_dict())
        return report
    finally:
        target.close()


def _source_unchanged(source: ReadOnlySQLite) -> bool:
    try:
        source.assert_unchanged()
        return True
    except RuntimeError:
        return False


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Dry-run, import, resume, and reconcile all legacy SQLite stores "
            "without modifying the sources or switching runtime backends."
        )
    )
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    result.add_argument(
        "--database-url",
        default=os.getenv("POSTGRES_IMPORT_DATABASE_URL"),
        required=not bool(os.getenv("POSTGRES_IMPORT_DATABASE_URL")),
    )
    result.add_argument("--market-sqlite", required=True)
    result.add_argument("--research-sqlite", required=True)
    result.add_argument("--portfolio-sqlite", required=True)
    result.add_argument("--research-source-root", required=True)
    result.add_argument("--research-target-artifact-root", required=True)
    result.add_argument(
        "--research-runtime-artifact-root",
        default="/app/artifacts",
    )
    result.add_argument(
        "--tenant-id",
        default="00000000-0000-7000-8000-000000000001",
    )
    result.add_argument("--operator-id", default="local-operator")
    result.add_argument("--user-map", default="")
    result.add_argument("--accept-deferred", action="append")
    result.add_argument("--verified-backup-manifest")
    result.add_argument("--resume")
    result.add_argument("--stop-after-table")
    result.add_argument("--report-file")
    return result


def main() -> int:
    args = parser().parse_args()
    mode = "apply" if args.apply else "dry-run"
    try:
        if mode == "apply":
            verified_backup(args.verified_backup_manifest)
            preflight = run_import(args, mode="dry-run")
            if preflight.blocked:
                payload = preflight.as_dict()
                payload["apply_refused"] = (
                    "dry-run validation must be clean before target writes"
                )
                print(canonical_json(payload))
                return 2
        report = run_import(args, mode=mode)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(canonical_json({"error": str(error), "mode": mode}))
        return 2
    payload = report.as_dict()
    output = json.dumps(payload, indent=2, sort_keys=True, default=str)
    print(output)
    if args.report_file:
        report_path = Path(args.report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(f"{output}\n")
    if mode == "apply" and not report.reconciled:
        return 3 if report.warnings and not report.errors else 2
    return 1 if report.blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
