from __future__ import annotations

import uuid
from decimal import Decimal

from importer.common import (
    ReadOnlySQLite,
    checksum,
    decimal_value,
    remap_user,
    source_key,
    utc_datetime,
)
from importer.report import ImportReport
from importer.target import Target

TABLE_ORDER = (
    "users",
    "credential_secrets",
    "portfolio",
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
)

TARGET_TABLES = {
    "portfolio": "portfolios",
    **{name: name for name in TABLE_ORDER if name != "portfolio"},
}

TARGET_TO_SOURCE = {
    target: source for source, target in TARGET_TABLES.items()
}

FINANCIAL_FIELDS = {
    "portfolios": (
        "balance",
        "equity",
        "total_pnl",
        "peak_equity",
        "max_drawdown",
    ),
    "trades": (
        "quantity",
        "realized_quantity",
        "entry_fees",
        "entry_slippage",
        "entry_costs_allocated",
        "exit_fees_total",
        "exit_slippage_total",
        "costs_total",
        "gross_pnl",
        "pnl",
    ),
    "paper_orders": (
        "quantity",
        "filled_quantity",
        "reserved_cash",
        "filled_notional",
        "fees_total",
        "slippage_total",
    ),
    "paper_order_fills": ("quantity", "price", "fees", "slippage"),
    "live_orders": (
        "quantity",
        "filled_quantity",
        "estimated_notional",
        "average_fill_price",
    ),
    "live_order_fills": ("quantity", "price"),
    "equity_snapshots": ("equity", "balance"),
    "risk_snapshots": ("equity",),
}


def _mapping_id(
    target: Target,
    source_table: str,
    value: object,
) -> str:
    mapped = target.lookup_mapping("portfolio", source_table, value)
    if mapped is None:
        raise ValueError(
            f"missing ID mapping for portfolio.{source_table}:{value}"
        )
    return mapped


def _preallocate_mappings(
    source: ReadOnlySQLite,
    target: Target,
    report: ImportReport,
) -> None:
    for source_table in TABLE_ORDER:
        if source_table not in source.tables():
            continue
        target_table = TARGET_TABLES[source_table]
        columns = target.table_columns("portfolio", target_table)
        if "id" not in columns:
            continue
        target_id_type = str(columns["id"]["data_type"])
        for row in source.rows(source_table):
            if "id" not in row:
                continue
            try:
                target.mapping(
                    source_store="portfolio",
                    source_table=source_table,
                    source_key=source_key(row["id"]),
                    source_row_checksum=checksum(row),
                    target_schema="portfolio",
                    target_table=target_table,
                    target_id_override=(
                        str(row["id"])
                        if target_id_type != "uuid"
                        else None
                    ),
                )
            except ValueError as error:
                report.errors.append(
                    f"portfolio.{source_table} row {row.get('id')}: {error}"
                )
    target.commit()


def _explicit_reference(
    target: Target,
    source_table: str,
    column: str,
    value: object,
    row: dict[str, object],
) -> str | None:
    if value is None:
        return None
    if source_table == "action_items" and column == "trade_id":
        return _mapping_id(target, "trades", value)
    if source_table == "action_items" and column == "order_id":
        source_type = str(row.get("source_type") or "").lower()
        preferred = (
            ("live_orders", "paper_orders")
            if "live" in source_type
            else ("paper_orders", "live_orders")
        )
        for candidate in preferred:
            mapped = target.lookup_mapping("portfolio", candidate, value)
            if mapped is not None:
                return mapped
        raise ValueError(f"action item order {value} has no mapped order")
    return None


def _convert_value(
    value: object,
    column: dict[str, object],
    *,
    label: str,
    report: ImportReport,
) -> object:
    if value is None:
        return None
    data_type = str(column["data_type"])
    if data_type == "uuid":
        return str(uuid.UUID(str(value)))
    if data_type == "numeric":
        return decimal_value(value, int(column["numeric_scale"] or 0))
    if data_type.startswith("timestamp"):
        parsed, assumed_utc = utc_datetime(value)
        if assumed_utc:
            report.warnings.append(f"{label} timestamp assumed UTC")
        return parsed
    if data_type == "boolean":
        return bool(value)
    if data_type in {"integer", "bigint", "smallint"}:
        return int(value)
    if data_type in {"double precision", "real"}:
        return float(value)
    return value


def _transform_row(
    target: Target,
    report: ImportReport,
    source_table: str,
    row: dict[str, object],
    user_map: dict[str, str],
) -> dict[str, object]:
    target_table = TARGET_TABLES[source_table]
    columns = target.table_columns("portfolio", target_table)
    foreign_keys = target.foreign_keys("portfolio", target_table)
    values: dict[str, object] = {}
    for name, column in columns.items():
        if name == "tenant_id":
            values[name] = target.tenant_id
            continue
        if name == "id" and "id" in row:
            if str(column["data_type"]) == "uuid":
                values[name] = _mapping_id(
                    target,
                    source_table,
                    row["id"],
                )
            else:
                values[name] = int(row["id"])
            continue
        if name == "user_key" and name in row:
            values[name] = remap_user(row[name], user_map)
            continue
        if name not in row:
            continue
        value = row[name]
        explicit = _explicit_reference(
            target,
            source_table,
            name,
            value,
            row,
        )
        if explicit is not None:
            value = explicit
        elif name in foreign_keys and value is not None:
            referenced_target = foreign_keys[name][1]
            referenced_source = TARGET_TO_SOURCE.get(referenced_target)
            if referenced_source is None:
                raise ValueError(
                    f"unsupported reference {target_table}.{name} "
                    f"to {referenced_target}"
                )
            value = _mapping_id(target, referenced_source, value)
        values[name] = _convert_value(
            value,
            column,
            label=f"portfolio.{source_table}.{name}",
            report=report,
        )
    if source_table == "live_trading_control":
        values.update(
            {
                "acknowledged": False,
                "acknowledged_by": None,
                "acknowledged_at": None,
                "acknowledgement_note": None,
                "trading_disabled": True,
                "disabled_reason": (
                    "SQLite import requires explicit operator re-acknowledgement"
                ),
                "disabled_by": "postgresql-import",
                "disabled_at": values.get("updated_at"),
            }
        )
        report.warnings.append(
            "portfolio.live_trading_control was forced fail-closed; "
            "live execution requires a new operator acknowledgement"
        )
    return values


def _comparable(
    values: dict[str, object],
) -> dict[str, object]:
    return {
        key: (
            format(value, "f")
            if isinstance(value, Decimal)
            else value.isoformat()
            if hasattr(value, "isoformat")
            else str(value)
            if isinstance(value, uuid.UUID)
            else value
        )
        for key, value in values.items()
    }


def import_portfolio(
    source: ReadOnlySQLite,
    target: Target,
    report: ImportReport,
    *,
    user_map: dict[str, str],
    stop_after_table: str | None = None,
) -> None:
    source_tables = set(source.tables())
    unexpected = source_tables - set(TABLE_ORDER)
    if unexpected:
        report.decisions.append(
            "Portfolio source has unmapped tables: "
            + ", ".join(sorted(unexpected))
        )
    _preallocate_mappings(source, target, report)
    financial: dict[str, dict[str, str]] = {}
    for source_table in TABLE_ORDER:
        if source_table not in source_tables:
            report.warnings.append(
                f"portfolio.{source_table} is absent and was treated as empty"
            )
            continue
        target_table = TARGET_TABLES[source_table]
        rows = source.rows(source_table)
        table_report = report.table("portfolio", source_table)
        table_report.source_rows = len(rows)
        table_report.source_checksum = checksum(rows)
        transformed_rows: list[dict[str, object]] = []
        aggregates = {
            field: Decimal(0)
            for field in FINANCIAL_FIELDS.get(target_table, ())
        }
        for row in rows:
            try:
                values = _transform_row(
                    target,
                    report,
                    source_table,
                    row,
                    user_map,
                )
                transformed_rows.append(_comparable(values))
                for field in aggregates:
                    if values.get(field) is not None:
                        aggregates[field] += Decimal(str(values[field]))
                existing = (
                    target.fetch_by_id(
                        "portfolio",
                        target_table,
                        "id",
                        values["id"],
                    )
                    if target.mode == "apply"
                    else None
                )
                if existing is not None:
                    actual = {
                        key: existing[key]
                        for key in values
                        if key in existing
                    }
                    if checksum(_comparable(actual)) != checksum(
                        _comparable(values)
                    ):
                        raise ValueError(
                            f"target row conflicts for id {row.get('id')}"
                        )
                    table_report.skipped_rows += 1
                elif target.mode == "apply":
                    target.insert("portfolio", target_table, values)
                    persisted = target.fetch_by_id(
                        "portfolio",
                        target_table,
                        "id",
                        values["id"],
                    )
                    if persisted is None:
                        raise ValueError(
                            f"target row was not persisted for id {row.get('id')}"
                        )
                    persisted_values = {
                        key: persisted[key]
                        for key in values
                        if key in persisted
                    }
                    if checksum(_comparable(persisted_values)) != checksum(
                        _comparable(values)
                    ):
                        raise ValueError(
                            f"persisted target row differs for id {row.get('id')}"
                        )
                    table_report.imported_rows += 1
                else:
                    table_report.imported_rows += 1
            except (KeyError, TypeError, ValueError) as error:
                target.rollback()
                table_report.conflict_rows += 1
                report.errors.append(
                    f"portfolio.{source_table} row {row.get('id')}: {error}"
                )
        table_report.target_checksum = checksum(transformed_rows)
        if target.mode == "apply":
            target_count = target.count("portfolio", target_table)
            if target_count != len(rows):
                report.errors.append(
                    f"portfolio.{source_table} target count mismatch: "
                    f"source={len(rows)}, target={target_count}"
                )
        if aggregates:
            financial[source_table] = {
                field: format(value, "f")
                for field, value in aggregates.items()
            }
        target.update_progress(
            source_store="portfolio",
            source_table=source_table,
            status="failed" if table_report.conflict_rows else "completed",
            last_source_key=(
                source_key(rows[-1]["id"]) if rows else None
            ),
            source_rows=table_report.source_rows,
            imported_rows=table_report.imported_rows,
            skipped_rows=table_report.skipped_rows,
            conflict_rows=table_report.conflict_rows,
            source_checksum=table_report.source_checksum,
            target_checksum=table_report.target_checksum,
        )
        target.commit()
        if stop_after_table == f"portfolio.{source_table}":
            raise InterruptedError(
                f"Stopped after portfolio.{source_table} for resume testing"
            )
    report.financial_reconciliation = financial
