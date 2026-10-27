from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

EXPECTED_REVISIONS = {
    "alembic_version": "0003_data",
    "alembic_version_research": "0002_research",
    "alembic_version_portfolio": "0002_portfolio",
}
PRESERVED_EVIDENCE = (
    "portfolio.trades",
    "portfolio.trade_executions",
    "portfolio.trade_journals",
    "portfolio.paper_orders",
    "portfolio.paper_order_fills",
    "portfolio.paper_order_events",
    "portfolio.live_orders",
    "portfolio.live_order_fills",
    "portfolio.live_execution_audit",
    "operations.import_runs",
    "operations.import_mappings",
)
MAINTENANCE_TABLES = (
    "market_data.candles",
    "research.backtest_runs",
    "research.backtest_artifacts",
    "portfolio.equity_snapshots",
    *PRESERVED_EVIDENCE,
)


def database_url() -> str:
    value = os.getenv("POSTGRES_OPERATIONS_DATABASE_URL", "")
    if not value:
        raise ValueError("POSTGRES_OPERATIONS_DATABASE_URL is required")
    return value


def connection(database: str | None = None, *, autocommit: bool = False):
    url = database_url()
    if database:
        url = url.rsplit("/", 1)[0] + f"/{database}"
    return psycopg.connect(url, autocommit=autocommit, row_factory=dict_row)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def table_counts(conn) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT schemaname, relname
        FROM pg_stat_user_tables
        WHERE schemaname IN
            ('market_data', 'research', 'portfolio', 'operations')
        ORDER BY schemaname, relname
        """
    ).fetchall()
    result: dict[str, int] = {}
    for row in rows:
        name = f"{row['schemaname']}.{row['relname']}"
        result[name] = int(
            conn.execute(
                sql.SQL("SELECT count(*) AS count FROM {}.{}").format(
                    sql.Identifier(row["schemaname"]),
                    sql.Identifier(row["relname"]),
                )
            ).fetchone()["count"]
        )
    return result


def manifest_path(directory: Path) -> Path:
    return directory / "manifest.json"


def verify_manifest(directory: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path(directory).read_text())
    dump_name = Path(str(manifest["dump_file"]))
    if dump_name.is_absolute() or dump_name.name != str(dump_name):
        raise ValueError("PostgreSQL backup manifest has an unsafe dump path")
    dump = directory / dump_name
    if not dump.is_file() or sha256(dump) != manifest["sha256"]:
        raise ValueError("PostgreSQL backup checksum verification failed")
    subprocess.run(
        ["pg_restore", "--list", str(dump)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return manifest


def backup(args: argparse.Namespace) -> None:
    directory = Path(args.directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    dump = directory / "database.dump"
    with connection() as conn:
        conn.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )
        snapshot = conn.execute(
            "SELECT pg_export_snapshot() AS value"
        ).fetchone()["value"]
        run(
            [
                "pg_dump",
                "--dbname",
                database_url(),
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--snapshot",
                snapshot,
                "--file",
                str(dump),
            ]
        )
        manifest = {
            "format_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "database": conn.execute(
                "SELECT current_database() AS name"
            ).fetchone()["name"],
            "dump_file": dump.name,
            "sha256": sha256(dump),
            "byte_size": dump.stat().st_size,
            "postgres_version": conn.execute(
                "SHOW server_version"
            ).fetchone()["server_version"],
            "revisions": {
                table: conn.execute(
                    sql.SQL("SELECT version_num FROM {}").format(
                        sql.Identifier(table)
                    )
                ).fetchone()["version_num"]
                for table in EXPECTED_REVISIONS
            },
            "table_counts": table_counts(conn),
        }
    manifest_path(directory).write_text(json.dumps(manifest, indent=2) + "\n")
    os.chmod(dump, 0o600)
    os.chmod(manifest_path(directory), 0o600)
    verify_manifest(directory)
    print(json.dumps(manifest, indent=2))


def verify(args: argparse.Namespace) -> None:
    print(json.dumps(verify_manifest(Path(args.directory)), indent=2))


def restore_drill(args: argparse.Namespace) -> None:
    directory = Path(args.directory)
    manifest = verify_manifest(directory)
    name = f"restore_drill_{uuid.uuid4().hex[:12]}"
    dump = directory / str(manifest["dump_file"])
    try:
        with connection("postgres", autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        run(
            [
                "pg_restore",
                "--dbname",
                database_url().rsplit("/", 1)[0] + f"/{name}",
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                str(dump),
            ]
        )
        with connection(name) as conn:
            revisions = {
                table: conn.execute(
                    sql.SQL("SELECT version_num FROM {}").format(
                        sql.Identifier(table)
                    )
                ).fetchone()["version_num"]
                for table in EXPECTED_REVISIONS
            }
            counts = table_counts(conn)
        if revisions != manifest["revisions"] or counts != manifest["table_counts"]:
            raise RuntimeError("Isolated restore reconciliation failed")
        print(
            json.dumps(
                {
                    "status": "verified",
                    "database": name,
                    "revisions": revisions,
                    "table_counts": counts,
                },
                indent=2,
            )
        )
    finally:
        with connection("postgres", autocommit=True) as conn:
            conn.execute(
                sql.SQL(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s"
                ),
                (name,),
            )
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name))
            )


def status(_: argparse.Namespace) -> None:
    with connection() as conn:
        tables = conn.execute(
            """
            SELECT schemaname, relname,
                   pg_total_relation_size(relid) AS total_bytes,
                   n_live_tup, n_dead_tup,
                   last_vacuum, last_autovacuum, last_analyze, last_autoanalyze
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            """
        ).fetchall()
        activity = conn.execute(
            """
            SELECT pid, usename, application_name, state,
                   extract(epoch FROM (clock_timestamp() - query_start)) AS seconds,
                   left(query, 240) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
              AND query_start IS NOT NULL
              AND clock_timestamp() - query_start > interval '5 seconds'
            ORDER BY query_start
            """
        ).fetchall()
        payload = {
            "captured_at": datetime.now(UTC).isoformat(),
            "database_bytes": conn.execute(
                "SELECT pg_database_size(current_database()) AS value"
            ).fetchone()["value"],
            "tables": tables,
            "long_running_queries": activity,
            "partition_review": [
                {
                    "table": f"{row['schemaname']}.{row['relname']}",
                    "total_bytes": row["total_bytes"],
                    "estimated_rows": row["n_live_tup"],
                }
                for row in tables
                if row["total_bytes"] >= 100 * 1024**3
                or row["n_live_tup"] >= 50_000_000
            ],
        }
    print(json.dumps(payload, indent=2, default=str))


def maintain(args: argparse.Namespace) -> None:
    tables = args.table or list(MAINTENANCE_TABLES)
    invalid = sorted(set(tables) - set(MAINTENANCE_TABLES))
    if invalid:
        raise ValueError(f"unsupported maintenance tables: {', '.join(invalid)}")
    with connection(autocommit=True) as conn:
        for table in tables:
            schema, name = table.split(".", 1)
            conn.execute(
                sql.SQL("VACUUM (ANALYZE) {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(name)
                )
            )
    print(json.dumps({"status": "completed", "tables": tables}, indent=2))


def years_before(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def retention(args: argparse.Namespace) -> None:
    now = datetime.fromisoformat(args.as_of)
    if now.tzinfo is None:
        raise ValueError("--as-of must include an explicit UTC offset")
    now = now.astimezone(UTC)
    if args.batch_size < 1:
        raise ValueError("--batch-size must be positive")
    policies = {
        ("1m", "5m", "15m"): now - timedelta(days=400),
        ("1h", "4h"): years_before(now, 7),
    }
    archive_manifest = None
    if args.apply:
        if not args.archive_directory:
            raise ValueError("--apply requires --archive-directory")
        archive_manifest = verify_manifest(Path(args.archive_directory))
    report: dict[str, object] = {
        "mode": "apply" if args.apply else "dry-run",
        "as_of": now.isoformat(),
        "preserved_evidence_tables": list(PRESERVED_EVIDENCE),
        "policies": [],
    }
    with connection() as conn:
        if archive_manifest is not None:
            revisions = {
                table: conn.execute(
                    sql.SQL("SELECT version_num FROM {}").format(
                        sql.Identifier(table)
                    )
                ).fetchone()["version_num"]
                for table in EXPECTED_REVISIONS
            }
            if revisions != archive_manifest["revisions"]:
                raise ValueError(
                    "Archive revisions do not match the active database"
                )
            if table_counts(conn) != archive_manifest["table_counts"]:
                raise ValueError(
                    "Archive table counts do not match the active database; "
                    "create and verify a fresh backup before retention"
                )
        for intervals, cutoff in policies.items():
            count = conn.execute(
                """
                SELECT count(*) AS count
                FROM market_data.candles
                WHERE bar_interval = ANY(%s) AND bucket_start < %s
                """,
                (list(intervals), cutoff),
            ).fetchone()["count"]
            deleted = 0
            if args.apply:
                deleted = conn.execute(
                    """
                    DELETE FROM market_data.candles
                    WHERE ctid IN (
                        SELECT ctid FROM market_data.candles
                        WHERE bar_interval = ANY(%s) AND bucket_start < %s
                        ORDER BY bucket_start
                        LIMIT %s
                    )
                    """,
                    (list(intervals), cutoff, args.batch_size),
                ).rowcount
            report["policies"].append(
                {
                    "intervals": list(intervals),
                    "cutoff": cutoff.isoformat(),
                    "eligible_rows": count,
                    "deleted_rows": deleted,
                }
            )
    print(json.dumps(report, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    for name, function in (
        ("backup", backup),
        ("verify", verify),
        ("restore-drill", restore_drill),
    ):
        command = commands.add_parser(name)
        command.add_argument("--directory", required=True)
        command.set_defaults(function=function)
    command = commands.add_parser("status")
    command.set_defaults(function=status)
    command = commands.add_parser("maintain")
    command.add_argument("--table", action="append")
    command.set_defaults(function=maintain)
    command = commands.add_parser("retention")
    command.add_argument("--as-of", required=True)
    command.add_argument("--apply", action="store_true")
    command.add_argument("--archive-directory")
    command.add_argument("--batch-size", type=int, default=10_000)
    command.set_defaults(function=retention)
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        args.function(args)
        return 0
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        psycopg.Error,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
