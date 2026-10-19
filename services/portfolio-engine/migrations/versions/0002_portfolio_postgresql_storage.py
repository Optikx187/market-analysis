"""PostgreSQL portfolio storage

Revision ID: 0002_portfolio
Revises: 0001_portfolio
Create Date: 2026-10-18
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

from app.database import Base
from app import models  # noqa: F401


revision: str = "0002_portfolio"
down_revision: Union[str, Sequence[str], None] = "0001_portfolio"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TENANT_TABLES = (
    "trades",
    "trade_executions",
    "trade_journals",
    "paper_orders",
    "paper_order_fills",
    "paper_order_events",
    "portfolio",
    "equity_snapshots",
    "risk_snapshots",
    "alert_logs",
    "action_items",
    "dashboard_preferences",
    "live_orders",
    "live_order_fills",
    "live_execution_audit",
)

IMMUTABLE_TABLES = (
    "trade_executions",
    "trade_journals",
    "paper_order_fills",
    "paper_order_events",
    "live_order_fills",
    "live_execution_audit",
)


def _upgrade_postgresql() -> None:
    connection = op.get_bind()
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS portfolio"))
    Base.metadata.create_all(bind=connection, checkfirst=True)
    connection.execute(text(
        """
        CREATE INDEX IF NOT EXISTS idx_paper_orders_open
        ON portfolio.paper_orders (
            tenant_id,
            user_key,
            created_at DESC
        )
        WHERE status IN ('pending', 'submitted', 'partially_filled')
        """
    ))
    connection.execute(text(
        """
        CREATE INDEX IF NOT EXISTS idx_live_orders_open
        ON portfolio.live_orders (
            tenant_id,
            user_key,
            created_at DESC
        )
        WHERE status IN ('new', 'accepted', 'partially_filled', 'pending_cancel')
        """
    ))
    connection.execute(text(
        """
        CREATE OR REPLACE FUNCTION portfolio.reject_immutable_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$
        """
    ))
    for table_name in IMMUTABLE_TABLES:
        connection.execute(text(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable "
            f"ON portfolio.{table_name}"
        ))
        connection.execute(text(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON portfolio.{table_name}
            FOR EACH ROW
            EXECUTE FUNCTION portfolio.reject_immutable_mutation()
            """
        ))


def _upgrade_sqlite() -> None:
    connection = op.get_bind()
    models.RiskSnapshot.__table__.create(bind=connection, checkfirst=True)
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    for table_name in TENANT_TABLES:
        if table_name not in tables:
            continue
        columns = {
            column["name"]
            for column in inspect(connection).get_columns(table_name)
        }
        if "tenant_id" not in columns:
            connection.execute(text(
                f"ALTER TABLE {table_name} ADD COLUMN tenant_id "
                "VARCHAR(36) NOT NULL "
                "DEFAULT '00000000-0000-7000-8000-000000000001'"
            ))
        connection.execute(text(
            f"CREATE INDEX IF NOT EXISTS ix_{table_name}_tenant_id "
            f"ON {table_name} (tenant_id)"
        ))


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _upgrade_postgresql()
    else:
        _upgrade_sqlite()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP SCHEMA IF EXISTS portfolio CASCADE")
        return
    raise RuntimeError(
        "The tenant-aware SQLite transition migration is not downgraded in place. "
        "Restore the verified pre-migration backup instead."
    )
