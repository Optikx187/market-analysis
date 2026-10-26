#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

TENANT_ID = "00000000-0000-7000-8000-000000000001"


def insert(
    connection: sqlite3.Connection,
    table: str,
    values: dict[str, object],
) -> None:
    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f'INSERT INTO "{table}" ({", ".join(columns)}) '
        f"VALUES ({placeholders})",
        [values[column] for column in columns],
    )


def seed_market(root: Path) -> None:
    connection = sqlite3.connect(root / "market" / "data_ingestion.db")
    insert(
        connection,
        "assets",
        {
            "id": 1,
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "asset_type": "STOCK",
            "is_active": 1,
            "created_at": "2026-01-01 12:00:00",
        },
    )
    insert(
        connection,
        "assets",
        {
            "id": 2,
            "ticker": "BTC",
            "name": "Bitcoin",
            "asset_type": "CRYPTO",
            "is_active": 1,
            "created_at": "2026-01-01 12:00:00",
        },
    )
    for values in (
        {
            "id": 1,
            "ticker": "AAPL",
            "timestamp": "2026-01-02 00:00:00",
            "open": 100,
            "high": 110,
            "low": 95,
            "close": 105,
            "volume": 1234.5,
            "interval": "1d",
        },
        {
            "id": 2,
            "ticker": "BTC",
            "timestamp": "2026-01-02T00:00:00+00:00",
            "open": 50000,
            "high": 51000,
            "low": 49000,
            "close": 50500,
            "volume": 12.345678901234,
            "interval": "1d",
        },
    ):
        insert(connection, "candles", values)
    insert(
        connection,
        "price_alerts",
        {
            "id": 1,
            "ticker": "AAPL",
            "condition": "above",
            "threshold": 120,
            "triggered": 0,
            "created_at": "2026-01-01 12:00:00",
        },
    )
    connection.commit()
    connection.close()


def seed_research(root: Path) -> None:
    artifact = root / "research" / "artifacts" / "legacy-result.json"
    result = {
        "run_id": "11111111-1111-4111-8111-111111111111",
        "ticker": "AAPL",
        "metrics": {"net_return": 0.123},
    }
    payload = json.dumps(result, sort_keys=True).encode()
    artifact.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    manifest = [
        {
            "artifact_key": "full-result",
            "media_type": "application/json",
            "uri": artifact.as_uri(),
            "sha256": digest,
            "byte_size": len(payload),
        }
    ]
    connection = sqlite3.connect(root / "research" / "backtests.db")
    insert(
        connection,
        "backtest_runs",
        {
            "run_id": result["run_id"],
            "ticker": "AAPL",
            "strategy_version": "strategy-v1",
            "created_at": "2026-01-03 12:00:00",
            "request_json": json.dumps({"ticker": "AAPL"}),
            "result_json": payload.decode(),
            "alert_eligible": 1,
            "tenant_id": TENANT_ID,
            "user_key": "alice",
            "module_key": "walk-forward-backtest",
            "module_version": "walk-forward-v1",
            "status": "completed",
            "input_lineage_json": json.dumps(
                {"primary": {"ticker": "AAPL", "row_count": 1}}
            ),
            "artifact_manifest_json": json.dumps(manifest),
            "retention_class": "recommendation-evidence",
            "retain_until": "2033-01-03 12:00:00",
        },
    )
    insert(
        connection,
        "backtest_artifacts",
        {
            "id": "legacy-artifact",
            "tenant_id": TENANT_ID,
            "user_key": "alice",
            "run_id": result["run_id"],
            "artifact_key": "full-result",
            "media_type": "application/json",
            "uri": artifact.as_uri(),
            "sha256": digest,
            "byte_size": len(payload),
            "created_at": "2026-01-03 12:00:00",
            "retain_until": "2033-01-03 12:00:00",
        },
    )
    connection.commit()
    connection.close()


def portfolio_rows() -> dict[str, list[dict[str, object]]]:
    tenant = TENANT_ID
    return {
        "users": [
            {
                "id": 1,
                "username": "alice",
                "password_hash": "scrypt$fixture",
                "display_name": "Alice",
                "is_active": 1,
                "created_at": "2026-01-01 10:00:00",
            }
        ],
        "credential_secrets": [
            {
                "id": 1,
                "provider": "alpaca",
                "key": "ALPACA_API_KEY",
                "value": "gAAAAfixture",
                "verified": 1,
                "updated_at": "2026-01-01 10:00:00",
            }
        ],
        "portfolio": [
            {
                "id": 1,
                "user_key": "alice",
                "balance": 9876.54,
                "equity": 10023.45,
                "total_pnl": 23.45,
                "win_count": 1,
                "loss_count": 0,
                "max_drawdown": 0.012345678901,
                "peak_equity": 10100,
                "updated_at": "2026-01-04 12:00:00",
                "tenant_id": tenant,
            }
        ],
        "trades": [
            {
                "id": 1,
                "user_key": "alice",
                "ticker": "AAPL",
                "direction": "BUY",
                "entry_price": 100.123456789012,
                "exit_price": 110.123456789012,
                "quantity": 2.123456789012345678,
                "stop_loss": 95,
                "target_price": 115,
                "asset_type": "stock",
                "sector": "Technology",
                "market_regime": "bull",
                "volatility_regime": "normal",
                "breadth_regime": "strong",
                "risk_regime": "risk_on",
                "regime_label": "Bull",
                "timeframe_agreement": 100,
                "strategy_name": "fixture",
                "strategy_version": "v1",
                "timeframe": "1d",
                "signal_confidence": 80,
                "signal_context_json": "{}",
                "execution_context_json": "{}",
                "planned_entry_price": 100,
                "planned_exit_price": 110,
                "planned_quantity": 2.123456789012345678,
                "entry_fees": 0.11,
                "entry_slippage": 0.22,
                "entry_costs_allocated": 0.33,
                "exit_fees_total": 0.12,
                "exit_slippage_total": 0.23,
                "costs_total": 1.01,
                "realized_quantity": 2.123456789012345678,
                "gross_pnl": 22.456789012345,
                "mfe_usd": 30,
                "mae_usd": -5,
                "mfe_pct": 0.15,
                "mae_pct": -0.02,
                "excursion_status": "calculated",
                "status": "CLOSED",
                "pnl": 21.446789012345,
                "pnl_pct": 0.10,
                "opened_at": "2026-01-01 12:00:00",
                "closed_at": "2026-01-04 12:00:00",
                "tenant_id": tenant,
            }
        ],
        "trade_executions": [
            {
                "id": 1,
                "user_key": "alice",
                "trade_id": 1,
                "kind": "ENTRY",
                "price": 100.123456789012,
                "quantity": 2.123456789012345678,
                "fees": 0.11,
                "slippage": 0.22,
                "entry_costs_allocated": 0.33,
                "note": "entry",
                "executed_at": "2026-01-01 12:00:00",
                "tenant_id": tenant,
            },
            {
                "id": 2,
                "user_key": "alice",
                "trade_id": 1,
                "kind": "EXIT",
                "price": 110.123456789012,
                "quantity": 2.123456789012345678,
                "fees": 0.12,
                "slippage": 0.23,
                "entry_costs_allocated": 0.33,
                "gross_pnl": 22.456789012345,
                "net_pnl": 21.446789012345,
                "note": "exit",
                "executed_at": "2026-01-04 12:00:00",
                "tenant_id": tenant,
            },
        ],
        "trade_journals": [
            {
                "id": 1,
                "user_key": "alice",
                "trade_id": 1,
                "ticker": "AAPL",
                "strategy_name": "fixture",
                "outcome": "win",
                "net_pnl": 21.446789012345,
                "summary": "fixture journal",
                "journal_json": "{}",
                "created_at": "2026-01-04 12:00:00",
                "tenant_id": tenant,
            }
        ],
        "paper_orders": [
            {
                "id": 1,
                "user_key": "alice",
                "idempotency_key": "paper-1",
                "ticker": "AAPL",
                "asset_type": "stock",
                "side": "buy",
                "order_type": "limit",
                "role": "standalone",
                "status": "filled",
                "quantity": 2.123456789012345678,
                "filled_quantity": 2.123456789012345678,
                "limit_price": 100.123456789012,
                "triggered": 1,
                "time_in_force": "day",
                "reserved_cash": 0,
                "average_fill_price": 100.123456789012,
                "filled_notional": 212.607407169778,
                "fees_total": 0.11,
                "slippage_total": 0.22,
                "trade_id": 1,
                "created_at": "2026-01-01 12:00:00",
                "updated_at": "2026-01-01 12:01:00",
                "tenant_id": tenant,
            }
        ],
        "paper_order_fills": [
            {
                "id": 1,
                "user_key": "alice",
                "order_id": 1,
                "quantity": 2.123456789012345678,
                "price": 100.123456789012,
                "fees": 0.11,
                "slippage": 0.22,
                "candle_timestamp": "2026-01-01 12:00:00",
                "trade_id": 1,
                "created_at": "2026-01-01 12:01:00",
                "tenant_id": tenant,
            }
        ],
        "paper_order_events": [
            {
                "id": 1,
                "user_key": "alice",
                "order_id": 1,
                "event_type": "filled",
                "from_status": "pending",
                "to_status": "filled",
                "message": "filled",
                "detail_json": "{}",
                "created_at": "2026-01-01 12:01:00",
                "tenant_id": tenant,
            }
        ],
        "equity_snapshots": [
            {
                "id": 1,
                "user_key": "alice",
                "equity": 10023.45,
                "balance": 9876.54,
                "timestamp": "2026-01-04 12:00:00",
                "tenant_id": tenant,
            }
        ],
        "risk_snapshots": [
            {
                "id": 1,
                "tenant_id": tenant,
                "user_key": "alice",
                "equity": 10023.45,
                "snapshot_json": "{}",
                "created_at": "2026-01-04 12:00:00",
            }
        ],
        "alert_logs": [
            {
                "id": 1,
                "user_key": "alice",
                "ticker": "AAPL",
                "direction": "BUY",
                "status": "approved",
                "trigger_price": 100.123456789012,
                "approved": 1,
                "market_regime": "bull",
                "volatility_regime": "normal",
                "breadth_regime": "strong",
                "risk_regime": "risk_on",
                "regime_label": "Bull",
                "created_at": "2026-01-01 12:00:00",
                "tenant_id": tenant,
            }
        ],
        "action_items": [
            {
                "id": 1,
                "user_key": "alice",
                "source_key": "action-1",
                "source_type": "paper_order",
                "category": "risk",
                "severity": "medium",
                "is_mandatory": 0,
                "title": "Review",
                "message": "Review order",
                "ticker": "AAPL",
                "trade_id": 1,
                "order_id": 1,
                "deep_link_tab": "orders",
                "deep_link_json": "{}",
                "payload_json": "{}",
                "payload_hash": "hash",
                "status": "open",
                "source_active": 1,
                "first_seen_at": "2026-01-01 12:00:00",
                "last_seen_at": "2026-01-01 12:00:00",
                "updated_at": "2026-01-01 12:00:00",
                "tenant_id": tenant,
            }
        ],
        "dashboard_preferences": [
            {
                "id": 1,
                "user_key": "alice",
                "widgets_json": "[]",
                "mode": "detailed",
                "layouts_json": "{}",
                "updated_at": "2026-01-01 12:00:00",
                "tenant_id": tenant,
            }
        ],
        "live_trading_control": [
            {
                "id": 1,
                "acknowledged": 1,
                "acknowledged_by": "alice",
                "acknowledged_at": "2026-01-01 12:00:00",
                "acknowledgement_note": "fixture",
                "trading_disabled": 0,
                "updated_at": "2026-01-01 12:00:00",
            }
        ],
        "live_orders": [
            {
                "id": 1,
                "idempotency_key": "live-1",
                "client_order_id": "client-1",
                "broker": "alpaca",
                "broker_order_id": "broker-1",
                "broker_endpoint": "https://paper-api.alpaca.markets",
                "sandbox": 1,
                "user_key": "alice",
                "ticker": "AAPL",
                "asset_type": "stock",
                "side": "buy",
                "order_type": "limit",
                "quantity": 1.5,
                "filled_quantity": 1.5,
                "limit_price": 100,
                "time_in_force": "day",
                "reference_price": 100,
                "estimated_notional": 150,
                "average_fill_price": 100,
                "status": "filled",
                "preflight_json": "[]",
                "request_fingerprint": "fingerprint",
                "submitted_at": "2026-01-01 12:00:00",
                "reconciled_at": "2026-01-01 12:01:00",
                "broker_status_raw": "filled",
                "created_at": "2026-01-01 12:00:00",
                "updated_at": "2026-01-01 12:01:00",
                "tenant_id": tenant,
            }
        ],
        "live_order_fills": [
            {
                "id": 1,
                "user_key": "alice",
                "order_id": 1,
                "broker_fill_id": "fill-1",
                "quantity": 1.5,
                "price": 100,
                "filled_at": "2026-01-01 12:01:00",
                "created_at": "2026-01-01 12:01:00",
                "tenant_id": tenant,
            }
        ],
        "live_execution_audit": [
            {
                "id": 1,
                "user_key": "alice",
                "order_id": 1,
                "event_type": "filled",
                "actor": "fixture",
                "message": "filled",
                "record_json": "{}",
                "previous_hash": "",
                "entry_hash": "entryhash",
                "created_at": "2026-01-01 12:01:00",
                "tenant_id": tenant,
            }
        ],
    }


def seed_portfolio(root: Path) -> None:
    connection = sqlite3.connect(root / "portfolio" / "portfolio.db")
    for table, rows in portfolio_rows().items():
        for row in rows:
            insert(connection, table, row)
    connection.commit()
    connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    arguments = parser.parse_args()
    root = Path(arguments.root).resolve()
    (root / "market").mkdir(parents=True, exist_ok=True)
    (root / "research" / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "portfolio").mkdir(parents=True, exist_ok=True)
    seed_market(root)
    seed_research(root)
    seed_portfolio(root)


if __name__ == "__main__":
    main()
