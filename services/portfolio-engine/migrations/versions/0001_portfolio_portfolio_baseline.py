"""portfolio baseline

Revision ID: 0001_portfolio
Revises:
Create Date: 2026-10-12 14:30:35.233817
"""
from typing import Sequence, Union

from alembic import op as alembic_op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision: str = '0001_portfolio'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


class _BaselineOperations:
    def f(self, name: str):
        return alembic_op.f(name)

    def create_table(self, name: str, *elements, **kwargs):
        if name in inspect(alembic_op.get_bind()).get_table_names():
            return None
        return alembic_op.create_table(name, *elements, **kwargs)

    def create_index(
        self,
        name: str,
        table_name: str,
        columns: list[str],
        *,
        unique: bool,
    ):
        inspector = inspect(alembic_op.get_bind())
        if table_name not in inspector.get_table_names():
            return None
        available_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        if not set(columns).issubset(available_columns):
            return None
        existing_indexes = {
            index["name"] for index in inspector.get_indexes(table_name)
        }
        if name in existing_indexes:
            return None
        return alembic_op.create_index(
            name,
            table_name,
            columns,
            unique=unique,
        )


op = _BaselineOperations()


def _add_missing_columns(
    table_name: str,
    definitions: dict[str, str],
) -> None:
    connection = alembic_op.get_bind()
    columns = {
        column["name"]
        for column in inspect(connection).get_columns(table_name)
    }
    for name, definition in definitions.items():
        if name not in columns:
            connection.execute(text(
                f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}"
            ))


def _reconcile_legacy_schema() -> None:
    connection = alembic_op.get_bind()
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    user_scoped_tables = (
        "trades",
        "trade_executions",
        "trade_journals",
        "paper_orders",
        "paper_order_fills",
        "paper_order_events",
        "portfolio",
        "equity_snapshots",
        "alert_logs",
        "live_order_fills",
        "live_execution_audit",
    )
    for table_name in user_scoped_tables:
        if table_name not in tables:
            continue
        columns = {
            column["name"]
            for column in inspect(connection).get_columns(table_name)
        }
        if "user_key" not in columns:
            connection.execute(text(
                f"ALTER TABLE {table_name} ADD COLUMN user_key "
                "VARCHAR(120) NOT NULL DEFAULT 'default'"
            ))
        if table_name != "portfolio":
            connection.execute(text(
                f"CREATE INDEX IF NOT EXISTS ix_{table_name}_user_key "
                f"ON {table_name} (user_key)"
            ))

    if "portfolio" in tables:
        connection.execute(text(
            "DELETE FROM portfolio WHERE id NOT IN ("
            "SELECT MIN(id) FROM portfolio GROUP BY user_key)"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_user_key "
            "ON portfolio (user_key)"
        ))

    if "trades" in tables:
        _add_missing_columns("trades", {
            "asset_type": "VARCHAR(20) NOT NULL DEFAULT 'stock'",
            "sector": "VARCHAR(100) NOT NULL DEFAULT 'Unclassified'",
            "market_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "volatility_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "breadth_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "risk_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "regime_label": "VARCHAR(200) NOT NULL DEFAULT 'Unknown'",
            "timeframe_agreement": "FLOAT",
            "strategy_name": "VARCHAR(100)",
            "strategy_version": "VARCHAR(50)",
            "timeframe": "VARCHAR(20)",
            "signal_confidence": "FLOAT",
            "signal_context_json": "TEXT",
            "execution_context_json": "TEXT",
            "planned_entry_price": "FLOAT",
            "planned_exit_price": "FLOAT",
            "planned_quantity": "FLOAT",
            "entry_fees": "FLOAT NOT NULL DEFAULT 0",
            "entry_slippage": "FLOAT NOT NULL DEFAULT 0",
            "entry_costs_allocated": "FLOAT NOT NULL DEFAULT 0",
            "exit_fees_total": "FLOAT NOT NULL DEFAULT 0",
            "exit_slippage_total": "FLOAT NOT NULL DEFAULT 0",
            "costs_total": "FLOAT NOT NULL DEFAULT 0",
            "realized_quantity": "FLOAT NOT NULL DEFAULT 0",
            "gross_pnl": "FLOAT",
            "mfe_usd": "FLOAT",
            "mae_usd": "FLOAT",
            "mfe_pct": "FLOAT",
            "mae_pct": "FLOAT",
            "excursion_status": (
                "VARCHAR(20) NOT NULL DEFAULT 'not_calculated'"
            ),
        })
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("trades")
        }
        if {"quantity", "status", "pnl"}.issubset(columns):
            connection.execute(text(
                "UPDATE trades SET realized_quantity = quantity "
                "WHERE status = 'CLOSED' AND realized_quantity = 0"
            ))
            connection.execute(text(
                "UPDATE trades SET gross_pnl = pnl "
                "WHERE gross_pnl IS NULL AND pnl IS NOT NULL"
            ))

    if "paper_orders" in tables:
        _add_missing_columns("paper_orders", {
            "role": "VARCHAR(20) NOT NULL DEFAULT 'standalone'",
            "asset_type": "VARCHAR(20) NOT NULL DEFAULT 'stock'",
            "filled_quantity": "FLOAT NOT NULL DEFAULT 0",
            "limit_price": "FLOAT",
            "stop_price": "FLOAT",
            "trail_percent": "FLOAT",
            "trail_amount": "FLOAT",
            "trail_reference_price": "FLOAT",
            "effective_stop_price": "FLOAT",
            "triggered": "BOOLEAN NOT NULL DEFAULT 0",
            "triggered_at": "DATETIME",
            "time_in_force": "VARCHAR(10) NOT NULL DEFAULT 'gtc'",
            "expires_at": "DATETIME",
            "reference_price": "FLOAT",
            "reserved_cash": "FLOAT NOT NULL DEFAULT 0",
            "reservation_price": "FLOAT",
            "average_fill_price": "FLOAT",
            "filled_notional": "FLOAT NOT NULL DEFAULT 0",
            "fees_total": "FLOAT NOT NULL DEFAULT 0",
            "slippage_total": "FLOAT NOT NULL DEFAULT 0",
            "parent_id": "INTEGER",
            "oco_group": "VARCHAR(60)",
            "trade_id": "INTEGER",
            "last_candle_at": "DATETIME",
            "reject_reason": "TEXT",
            "cancel_reason": "TEXT",
            "created_at": "DATETIME",
            "updated_at": "DATETIME",
        })

    if "action_items" in tables:
        _add_missing_columns("action_items", {
            "user_key": "VARCHAR(120) NOT NULL DEFAULT 'default'",
            "source_type": "VARCHAR(40) NOT NULL DEFAULT 'operational'",
            "category": "VARCHAR(40) NOT NULL DEFAULT 'operations'",
            "severity": "VARCHAR(20) NOT NULL DEFAULT 'info'",
            "is_mandatory": "BOOLEAN NOT NULL DEFAULT 0",
            "title": "VARCHAR(200) NOT NULL DEFAULT ''",
            "message": "TEXT NOT NULL DEFAULT ''",
            "ticker": "VARCHAR(20)",
            "trade_id": "INTEGER",
            "order_id": "INTEGER",
            "context_id": "VARCHAR(120)",
            "deep_link_tab": "VARCHAR(40) NOT NULL DEFAULT 'settings'",
            "deep_link_json": "TEXT NOT NULL DEFAULT '{}'",
            "payload_json": "TEXT NOT NULL DEFAULT '{}'",
            "payload_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
            "status": "VARCHAR(20) NOT NULL DEFAULT 'open'",
            "source_active": "BOOLEAN NOT NULL DEFAULT 1",
            "snoozed_until": "DATETIME",
            "first_seen_at": "DATETIME",
            "last_seen_at": "DATETIME",
            "updated_at": "DATETIME",
            "acknowledged_at": "DATETIME",
            "snoozed_at": "DATETIME",
            "resolved_at": "DATETIME",
        })
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_action_item_source "
            "ON action_items (user_key, source_key)"
        ))

    if "dashboard_preferences" in tables:
        _add_missing_columns("dashboard_preferences", {
            "widgets_json": "TEXT NOT NULL DEFAULT '[]'",
            "mode": "VARCHAR(20) NOT NULL DEFAULT 'detailed'",
            "layouts_json": "TEXT NOT NULL DEFAULT '{}'",
            "updated_at": "DATETIME",
        })

    if "alert_logs" in tables:
        _add_missing_columns("alert_logs", {
            "approved": "BOOLEAN NOT NULL DEFAULT 1",
            "risk_decision_json": "TEXT",
            "market_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "volatility_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "breadth_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "risk_regime": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "regime_label": "VARCHAR(200) NOT NULL DEFAULT 'Unknown'",
            "timeframe_agreement": "FLOAT",
        })


def upgrade() -> None:
    if alembic_op.get_bind().dialect.name == "postgresql":
        return
    _reconcile_legacy_schema()
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('action_items',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('source_key', sa.String(length=200), nullable=False),
    sa.Column('source_type', sa.String(length=40), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=False),
    sa.Column('is_mandatory', sa.Boolean(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('ticker', sa.String(length=20), nullable=True),
    sa.Column('trade_id', sa.Integer(), nullable=True),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('context_id', sa.String(length=120), nullable=True),
    sa.Column('deep_link_tab', sa.String(length=40), nullable=False),
    sa.Column('deep_link_json', sa.Text(), nullable=False),
    sa.Column('payload_json', sa.Text(), nullable=False),
    sa.Column('payload_hash', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('source_active', sa.Boolean(), nullable=False),
    sa.Column('snoozed_until', sa.DateTime(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
    sa.Column('snoozed_at', sa.DateTime(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_key', 'source_key', name='uq_action_item_source')
    )
    op.create_index(op.f('ix_action_items_category'), 'action_items', ['category'], unique=False)
    op.create_index(op.f('ix_action_items_severity'), 'action_items', ['severity'], unique=False)
    op.create_index(op.f('ix_action_items_source_key'), 'action_items', ['source_key'], unique=False)
    op.create_index(op.f('ix_action_items_status'), 'action_items', ['status'], unique=False)
    op.create_index(op.f('ix_action_items_ticker'), 'action_items', ['ticker'], unique=False)
    op.create_index(op.f('ix_action_items_user_key'), 'action_items', ['user_key'], unique=False)
    op.create_table('alert_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('ticker', sa.String(length=20), nullable=False),
    sa.Column('direction', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('trigger_price', sa.Float(), nullable=False),
    sa.Column('stop_loss', sa.Float(), nullable=True),
    sa.Column('target_price', sa.Float(), nullable=True),
    sa.Column('optimal_size_usd', sa.Float(), nullable=True),
    sa.Column('kelly_pct', sa.Float(), nullable=True),
    sa.Column('capital_overspend', sa.Boolean(), nullable=True),
    sa.Column('approved', sa.Boolean(), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('risk_decision_json', sa.Text(), nullable=True),
    sa.Column('market_regime', sa.String(length=20), nullable=False),
    sa.Column('volatility_regime', sa.String(length=20), nullable=False),
    sa.Column('breadth_regime', sa.String(length=20), nullable=False),
    sa.Column('risk_regime', sa.String(length=20), nullable=False),
    sa.Column('regime_label', sa.String(length=200), nullable=False),
    sa.Column('timeframe_agreement', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_alert_logs_ticker'), 'alert_logs', ['ticker'], unique=False)
    op.create_index(op.f('ix_alert_logs_user_key'), 'alert_logs', ['user_key'], unique=False)
    op.create_table('credential_secrets',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_credential_secrets_key'), 'credential_secrets', ['key'], unique=True)
    op.create_index(op.f('ix_credential_secrets_provider'), 'credential_secrets', ['provider'], unique=False)
    op.create_table('dashboard_preferences',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('widgets_json', sa.Text(), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('layouts_json', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_dashboard_preferences_user_key'), 'dashboard_preferences', ['user_key'], unique=True)
    op.create_table('equity_snapshots',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('equity', sa.Float(), nullable=False),
    sa.Column('balance', sa.Float(), nullable=False),
    sa.Column('timestamp', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_equity_snapshots_user_key'), 'equity_snapshots', ['user_key'], unique=False)
    op.create_table('live_orders',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=False),
    sa.Column('client_order_id', sa.String(length=120), nullable=False),
    sa.Column('broker', sa.String(length=40), nullable=False),
    sa.Column('broker_order_id', sa.String(length=120), nullable=True),
    sa.Column('broker_endpoint', sa.String(length=200), nullable=False),
    sa.Column('sandbox', sa.Boolean(), nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('ticker', sa.String(length=20), nullable=False),
    sa.Column('asset_type', sa.String(length=20), nullable=False),
    sa.Column('side', sa.String(length=10), nullable=False),
    sa.Column('order_type', sa.String(length=20), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('filled_quantity', sa.Float(), nullable=False),
    sa.Column('limit_price', sa.Float(), nullable=True),
    sa.Column('stop_price', sa.Float(), nullable=True),
    sa.Column('time_in_force', sa.String(length=10), nullable=False),
    sa.Column('reference_price', sa.Float(), nullable=True),
    sa.Column('estimated_notional', sa.Float(), nullable=True),
    sa.Column('average_fill_price', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('preflight_json', sa.Text(), nullable=False),
    sa.Column('request_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('reject_reason', sa.Text(), nullable=True),
    sa.Column('cancel_reason', sa.Text(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(), nullable=True),
    sa.Column('reconciled_at', sa.DateTime(), nullable=True),
    sa.Column('broker_status_raw', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_live_orders_broker_order_id'), 'live_orders', ['broker_order_id'], unique=False)
    op.create_index(op.f('ix_live_orders_client_order_id'), 'live_orders', ['client_order_id'], unique=True)
    op.create_index(op.f('ix_live_orders_idempotency_key'), 'live_orders', ['idempotency_key'], unique=True)
    op.create_index(op.f('ix_live_orders_status'), 'live_orders', ['status'], unique=False)
    op.create_index(op.f('ix_live_orders_ticker'), 'live_orders', ['ticker'], unique=False)
    op.create_index(op.f('ix_live_orders_user_key'), 'live_orders', ['user_key'], unique=False)
    op.create_table('live_trading_control',
    sa.Column('id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('acknowledged', sa.Boolean(), nullable=False),
    sa.Column('acknowledged_by', sa.String(length=120), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
    sa.Column('acknowledgement_note', sa.Text(), nullable=True),
    sa.Column('trading_disabled', sa.Boolean(), nullable=False),
    sa.Column('disabled_reason', sa.Text(), nullable=True),
    sa.Column('disabled_by', sa.String(length=120), nullable=True),
    sa.Column('disabled_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('id = 1', name='ck_live_trading_control_singleton'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('portfolio',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('balance', sa.Float(), nullable=False),
    sa.Column('equity', sa.Float(), nullable=False),
    sa.Column('total_pnl', sa.Float(), nullable=True),
    sa.Column('win_count', sa.Integer(), nullable=True),
    sa.Column('loss_count', sa.Integer(), nullable=True),
    sa.Column('max_drawdown', sa.Float(), nullable=True),
    sa.Column('peak_equity', sa.Float(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_portfolio_user_key', 'portfolio', ['user_key'], unique=True)
    op.create_table('trades',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('ticker', sa.String(length=20), nullable=False),
    sa.Column('direction', sa.Enum('BUY', 'SELL', 'SUPPRESSED', name='signaldirection'), nullable=False),
    sa.Column('entry_price', sa.Float(), nullable=False),
    sa.Column('exit_price', sa.Float(), nullable=True),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('stop_loss', sa.Float(), nullable=False),
    sa.Column('target_price', sa.Float(), nullable=False),
    sa.Column('trailing_stop', sa.Float(), nullable=True),
    sa.Column('asset_type', sa.String(length=20), nullable=False),
    sa.Column('sector', sa.String(length=100), nullable=False),
    sa.Column('market_regime', sa.String(length=20), nullable=False),
    sa.Column('volatility_regime', sa.String(length=20), nullable=False),
    sa.Column('breadth_regime', sa.String(length=20), nullable=False),
    sa.Column('risk_regime', sa.String(length=20), nullable=False),
    sa.Column('regime_label', sa.String(length=200), nullable=False),
    sa.Column('timeframe_agreement', sa.Float(), nullable=True),
    sa.Column('strategy_name', sa.String(length=100), nullable=True),
    sa.Column('strategy_version', sa.String(length=50), nullable=True),
    sa.Column('timeframe', sa.String(length=20), nullable=True),
    sa.Column('signal_confidence', sa.Float(), nullable=True),
    sa.Column('signal_context_json', sa.Text(), nullable=True),
    sa.Column('execution_context_json', sa.Text(), nullable=True),
    sa.Column('planned_entry_price', sa.Float(), nullable=True),
    sa.Column('planned_exit_price', sa.Float(), nullable=True),
    sa.Column('planned_quantity', sa.Float(), nullable=True),
    sa.Column('entry_fees', sa.Float(), nullable=False),
    sa.Column('entry_slippage', sa.Float(), nullable=False),
    sa.Column('entry_costs_allocated', sa.Float(), nullable=False),
    sa.Column('exit_fees_total', sa.Float(), nullable=False),
    sa.Column('exit_slippage_total', sa.Float(), nullable=False),
    sa.Column('costs_total', sa.Float(), nullable=False),
    sa.Column('realized_quantity', sa.Float(), nullable=False),
    sa.Column('gross_pnl', sa.Float(), nullable=True),
    sa.Column('mfe_usd', sa.Float(), nullable=True),
    sa.Column('mae_usd', sa.Float(), nullable=True),
    sa.Column('mfe_pct', sa.Float(), nullable=True),
    sa.Column('mae_pct', sa.Float(), nullable=True),
    sa.Column('excursion_status', sa.String(length=20), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'CLOSED', name='tradestatus'), nullable=True),
    sa.Column('pnl', sa.Float(), nullable=True),
    sa.Column('pnl_pct', sa.Float(), nullable=True),
    sa.Column('opened_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_trades_ticker'), 'trades', ['ticker'], unique=False)
    op.create_index(op.f('ix_trades_user_key'), 'trades', ['user_key'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('username', sa.String(length=100), nullable=False),
    sa.Column('password_hash', sa.String(length=256), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_table('live_execution_audit',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('event_type', sa.String(length=40), nullable=False),
    sa.Column('actor', sa.String(length=120), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('record_json', sa.Text(), nullable=False),
    sa.Column('previous_hash', sa.String(length=64), nullable=False),
    sa.Column('entry_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['live_orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_live_execution_audit_event_type'), 'live_execution_audit', ['event_type'], unique=False)
    op.create_index(op.f('ix_live_execution_audit_order_id'), 'live_execution_audit', ['order_id'], unique=False)
    op.create_index(op.f('ix_live_execution_audit_user_key'), 'live_execution_audit', ['user_key'], unique=False)
    op.create_table('live_order_fills',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('broker_fill_id', sa.String(length=120), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('price', sa.Float(), nullable=False),
    sa.Column('filled_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['live_orders.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('order_id', 'broker_fill_id', name='uq_live_fill_broker_id')
    )
    op.create_index(op.f('ix_live_order_fills_order_id'), 'live_order_fills', ['order_id'], unique=False)
    op.create_index(op.f('ix_live_order_fills_user_key'), 'live_order_fills', ['user_key'], unique=False)
    op.create_table('paper_orders',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=False),
    sa.Column('ticker', sa.String(length=20), nullable=False),
    sa.Column('asset_type', sa.String(length=20), nullable=False),
    sa.Column('side', sa.String(length=10), nullable=False),
    sa.Column('order_type', sa.String(length=20), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('filled_quantity', sa.Float(), nullable=False),
    sa.Column('limit_price', sa.Float(), nullable=True),
    sa.Column('stop_price', sa.Float(), nullable=True),
    sa.Column('trail_percent', sa.Float(), nullable=True),
    sa.Column('trail_amount', sa.Float(), nullable=True),
    sa.Column('trail_reference_price', sa.Float(), nullable=True),
    sa.Column('effective_stop_price', sa.Float(), nullable=True),
    sa.Column('triggered', sa.Boolean(), nullable=False),
    sa.Column('triggered_at', sa.DateTime(), nullable=True),
    sa.Column('time_in_force', sa.String(length=10), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('reference_price', sa.Float(), nullable=True),
    sa.Column('reserved_cash', sa.Float(), nullable=False),
    sa.Column('reservation_price', sa.Float(), nullable=True),
    sa.Column('average_fill_price', sa.Float(), nullable=True),
    sa.Column('filled_notional', sa.Float(), nullable=False),
    sa.Column('fees_total', sa.Float(), nullable=False),
    sa.Column('slippage_total', sa.Float(), nullable=False),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('oco_group', sa.String(length=60), nullable=True),
    sa.Column('trade_id', sa.Integer(), nullable=True),
    sa.Column('last_candle_at', sa.DateTime(), nullable=True),
    sa.Column('reject_reason', sa.Text(), nullable=True),
    sa.Column('cancel_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['paper_orders.id'], ),
    sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_paper_orders_idempotency_key'), 'paper_orders', ['idempotency_key'], unique=True)
    op.create_index(op.f('ix_paper_orders_oco_group'), 'paper_orders', ['oco_group'], unique=False)
    op.create_index(op.f('ix_paper_orders_parent_id'), 'paper_orders', ['parent_id'], unique=False)
    op.create_index(op.f('ix_paper_orders_status'), 'paper_orders', ['status'], unique=False)
    op.create_index(op.f('ix_paper_orders_ticker'), 'paper_orders', ['ticker'], unique=False)
    op.create_index(op.f('ix_paper_orders_trade_id'), 'paper_orders', ['trade_id'], unique=False)
    op.create_index(op.f('ix_paper_orders_user_key'), 'paper_orders', ['user_key'], unique=False)
    op.create_table('trade_executions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('trade_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('ENTRY', 'EXIT', name='executionkind'), nullable=False),
    sa.Column('price', sa.Float(), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('fees', sa.Float(), nullable=False),
    sa.Column('slippage', sa.Float(), nullable=False),
    sa.Column('entry_costs_allocated', sa.Float(), nullable=False),
    sa.Column('gross_pnl', sa.Float(), nullable=True),
    sa.Column('net_pnl', sa.Float(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('executed_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_trade_executions_trade_id'), 'trade_executions', ['trade_id'], unique=False)
    op.create_index(op.f('ix_trade_executions_user_key'), 'trade_executions', ['user_key'], unique=False)
    op.create_table('trade_journals',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('trade_id', sa.Integer(), nullable=False),
    sa.Column('ticker', sa.String(length=20), nullable=False),
    sa.Column('strategy_name', sa.String(length=100), nullable=True),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('net_pnl', sa.Float(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('journal_json', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_trade_journals_ticker'), 'trade_journals', ['ticker'], unique=False)
    op.create_index(op.f('ix_trade_journals_trade_id'), 'trade_journals', ['trade_id'], unique=True)
    op.create_index(op.f('ix_trade_journals_user_key'), 'trade_journals', ['user_key'], unique=False)
    op.create_table('paper_order_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=40), nullable=False),
    sa.Column('from_status', sa.String(length=20), nullable=True),
    sa.Column('to_status', sa.String(length=20), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('detail_json', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['paper_orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_paper_order_events_order_id'), 'paper_order_events', ['order_id'], unique=False)
    op.create_index(op.f('ix_paper_order_events_user_key'), 'paper_order_events', ['user_key'], unique=False)
    op.create_table('paper_order_fills',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_key', sa.String(length=120), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('price', sa.Float(), nullable=False),
    sa.Column('fees', sa.Float(), nullable=False),
    sa.Column('slippage', sa.Float(), nullable=False),
    sa.Column('candle_timestamp', sa.DateTime(), nullable=False),
    sa.Column('trade_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['paper_orders.id'], ),
    sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_paper_order_fills_order_id'), 'paper_order_fills', ['order_id'], unique=False)
    op.create_index(op.f('ix_paper_order_fills_trade_id'), 'paper_order_fills', ['trade_id'], unique=False)
    op.create_index(op.f('ix_paper_order_fills_user_key'), 'paper_order_fills', ['user_key'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    if alembic_op.get_bind().dialect.name == "postgresql":
        return
    raise RuntimeError(
        "The portfolio baseline is destructive to downgrade. "
        "Restore the verified pre-migration backup instead."
    )
