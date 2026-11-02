from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime

from psycopg import sql
from psycopg.types.json import Jsonb

from importer.common import (
    ReadOnlySQLite,
    canonical_json,
    checksum,
    source_key,
    utc_datetime,
)
from importer.report import ImportReport
from importer.target import Target


def _json_object(value: object, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise TypeError(f"{label} must contain a JSON object")
    return parsed


def _time(value: object | None, *, nullable: bool = False) -> datetime | None:
    if nullable and value in {None, ""}:
        return None
    return utc_datetime(value)[0]


def _user(row: dict[str, object], mapping: dict[str, str]) -> str:
    source = str(row.get("user_key") or "default")
    return mapping.get(source, source)


def _uuid(value: object) -> str:
    return str(uuid.UUID(str(value)))


def _insert(
    target: Target,
    table: str,
    item: dict[str, object],
    conflict_columns: tuple[str, ...],
) -> bool:
    columns = tuple(item)
    statement = sql.SQL(
        "INSERT INTO research.{} ({}) VALUES ({}) "
        "ON CONFLICT ({}) DO NOTHING RETURNING 1"
    ).format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        sql.SQL(", ").join(map(sql.Identifier, conflict_columns)),
    )
    return (
        target.connection.execute(
            statement,
            [
                Jsonb(item[column])
                if column.endswith("_json")
                and item[column] is not None
                else item[column]
                for column in columns
            ],
        ).fetchone()
        is not None
    )


def _existing(
    target: Target,
    table: str,
    key: dict[str, object],
) -> dict[str, object] | None:
    clauses = [
        sql.SQL("{} = {}").format(sql.Identifier(column), sql.Placeholder())
        for column in key
    ]
    statement = sql.SQL("SELECT * FROM research.{} WHERE {}").format(
        sql.Identifier(table),
        sql.SQL(" AND ").join(clauses),
    )
    return target.connection.execute(
        statement,
        list(key.values()),
    ).fetchone()


def _canonical_subset(
    row: dict[str, object],
    columns: tuple[str, ...],
) -> dict[str, object]:
    return {
        column: (
            canonical_json(row[column])
            if column.endswith("_json") and row[column] is not None
            else str(row[column])
            if isinstance(row[column], (datetime, uuid.UUID))
            else row[column]
        )
        for column in columns
    }


def _import_table(
    source: ReadOnlySQLite,
    target: Target,
    report: ImportReport,
    *,
    table: str,
    transform: Callable[[dict[str, object]], dict[str, object]],
    conflict_columns: tuple[str, ...],
    compare_columns: tuple[str, ...],
) -> None:
    if table not in source.tables():
        return
    rows = source.rows(table)
    table_report = report.table("research", table)
    table_report.source_rows = len(rows)
    table_report.source_checksum = checksum(rows)
    normalized: list[dict[str, object]] = []
    for row in rows:
        try:
            item = transform(row)
            normalized.append(item)
            if target.mode == "apply":
                inserted = _insert(
                    target,
                    table,
                    item,
                    conflict_columns,
                )
                if inserted:
                    table_report.imported_rows += 1
                else:
                    existing = _existing(
                        target,
                        table,
                        {
                            column: item[column]
                            for column in conflict_columns
                        },
                    )
                    if existing is None or _canonical_subset(
                        existing,
                        compare_columns,
                    ) != _canonical_subset(item, compare_columns):
                        raise ValueError("target row conflicts")
                    table_report.skipped_rows += 1
            else:
                table_report.imported_rows += 1
            target.commit()
        except (KeyError, TypeError, ValueError) as error:
            target.rollback()
            table_report.conflict_rows += 1
            report.errors.append(
                f"research.{table} {row.get('run_id', '')}: {error}"
            )
    table_report.target_checksum = checksum(normalized)
    target.update_progress(
        source_store="research",
        source_table=table,
        status="failed" if table_report.conflict_rows else "completed",
        last_source_key=source_key(rows[-1]) if rows else None,
        source_rows=table_report.source_rows,
        imported_rows=table_report.imported_rows,
        skipped_rows=table_report.skipped_rows,
        conflict_rows=table_report.conflict_rows,
        source_checksum=table_report.source_checksum,
        target_checksum=table_report.target_checksum,
    )
    target.commit()


def import_scanner_state(
    source: ReadOnlySQLite,
    target: Target,
    report: ImportReport,
    *,
    user_map: dict[str, str],
) -> None:
    _import_table(
        source,
        target,
        report,
        table="scanner_configs",
        conflict_columns=("tenant_id", "user_key", "scanner_key"),
        compare_columns=(
            "enabled",
            "interval_minutes",
            "market_hours_only",
            "next_run_at",
        ),
        transform=lambda row: {
            "tenant_id": target.tenant_id,
            "user_key": _user(row, user_map),
            "scanner_key": str(row["scanner_key"]),
            "enabled": bool(row["enabled"]),
            "interval_minutes": int(row["interval_minutes"]),
            "market_hours_only": bool(row["market_hours_only"]),
            "next_run_at": _time(row["next_run_at"]),
            "updated_at": _time(row["updated_at"]),
        },
    )

    def run(row: dict[str, object]) -> dict[str, object]:
        run_id = _uuid(row["run_id"])
        target.mapping(
            source_store="research",
            source_table="scanner_runs",
            source_key=source_key(run_id),
            source_row_checksum=checksum(row),
            target_schema="research",
            target_table="scanner_runs",
            preserve_uuid=run_id,
        )
        return {
            "run_id": run_id,
            "tenant_id": target.tenant_id,
            "user_key": _user(row, user_map),
            "scanner_key": str(row["scanner_key"]),
            "idempotency_key": str(row["idempotency_key"]),
            "trigger_type": str(row["trigger_type"]),
            "status": str(row["status"]),
            "scheduled_for": _time(row["scheduled_for"]),
            "available_at": _time(row["available_at"]),
            "lease_owner": row.get("lease_owner"),
            "lease_expires_at": _time(
                row.get("lease_expires_at"), nullable=True
            ),
            "attempt_count": int(row.get("attempt_count") or 0),
            "result_json": (
                None
                if row.get("result_json") in {None, ""}
                else _json_object(row["result_json"], "result_json")
            ),
            "error": row.get("error"),
            "created_at": _time(row["created_at"]),
            "started_at": _time(row.get("started_at"), nullable=True),
            "completed_at": _time(row.get("completed_at"), nullable=True),
            "updated_at": _time(row["updated_at"]),
        }

    _import_table(
        source,
        target,
        report,
        table="scanner_runs",
        conflict_columns=("run_id",),
        compare_columns=(
            "tenant_id",
            "user_key",
            "idempotency_key",
            "status",
            "attempt_count",
            "result_json",
        ),
        transform=run,
    )
    _import_table(
        source,
        target,
        report,
        table="scanner_opportunity_actions",
        conflict_columns=("tenant_id", "user_key", "opportunity_id"),
        compare_columns=("action", "state_json"),
        transform=lambda row: {
            "tenant_id": target.tenant_id,
            "user_key": _user(row, user_map),
            "opportunity_id": str(row["opportunity_id"]),
            "action": str(row["action"]),
            "state_json": _json_object(row["state_json"], "state_json"),
            "updated_at": _time(row["updated_at"]),
        },
    )
    _import_table(
        source,
        target,
        report,
        table="scanner_notification_receipts",
        conflict_columns=("tenant_id", "user_key", "idempotency_key"),
        compare_columns=("run_id", "opportunity_id", "status"),
        transform=lambda row: {
            "tenant_id": target.tenant_id,
            "user_key": _user(row, user_map),
            "idempotency_key": str(row["idempotency_key"]),
            "run_id": _uuid(row["run_id"]),
            "opportunity_id": str(row["opportunity_id"]),
            "status": str(row["status"]),
            "error": row.get("error"),
            "created_at": _time(row["created_at"]),
            "updated_at": _time(row["updated_at"]),
        },
    )
