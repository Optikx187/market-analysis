"""Managed research and PostgreSQL backtest storage.

Revision ID: 0002_research
Revises: 0001_backtests
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0002_research"
down_revision: Union[str, Sequence[str], None] = "0001_backtests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_TENANT_ID = "00000000-0000-7000-8000-000000000001"


def _upgrade_sqlite() -> None:
    inspector = inspect(op.get_bind())
    existing_columns = {
        column["name"] for column in inspector.get_columns("backtest_runs")
    }
    additions = (
        sa.Column(
            "tenant_id",
            sa.Text(),
            nullable=False,
            server_default=DEFAULT_TENANT_ID,
        ),
        sa.Column(
            "user_key",
            sa.Text(),
            nullable=False,
            server_default="default",
        ),
        sa.Column(
            "module_key",
            sa.Text(),
            nullable=False,
            server_default="walk-forward-backtest",
        ),
        sa.Column(
            "module_version",
            sa.Text(),
            nullable=False,
            server_default="legacy-v1",
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="completed"),
        sa.Column(
            "input_lineage_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "artifact_manifest_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "retention_class",
            sa.Text(),
            nullable=False,
            server_default="legacy-research",
        ),
        sa.Column("retain_until", sa.Text(), nullable=True),
    )
    for column in additions:
        if column.name not in existing_columns:
            op.add_column("backtest_runs", column)
    op.execute(
        "UPDATE backtest_runs SET retain_until = "
        "CASE WHEN alert_eligible = 1 "
        "THEN datetime(created_at, '+7 years') "
        "ELSE datetime(created_at, '+2 years') END "
        "WHERE retain_until IS NULL"
    )
    inspector = inspect(op.get_bind())
    if "backtest_artifacts" not in inspector.get_table_names():
        op.create_table(
            "backtest_artifacts",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("user_key", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("artifact_key", sa.Text(), nullable=False),
            sa.Column("media_type", sa.Text(), nullable=False),
            sa.Column("uri", sa.Text(), nullable=False),
            sa.Column("sha256", sa.Text(), nullable=False),
            sa.Column("byte_size", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("retain_until", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["run_id"],
                ["backtest_runs.run_id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "user_key",
                "run_id",
                "artifact_key",
                name="uq_backtest_artifact_key",
            ),
        )
        op.create_index(
            "idx_backtest_artifacts_retention",
            "backtest_artifacts",
            ["tenant_id", "retain_until"],
        )
    indexes = {
        index["name"] for index in inspect(op.get_bind()).get_indexes(
            "backtest_runs"
        )
    }
    if "idx_backtest_runs_tenant_user_created" not in indexes:
        op.create_index(
            "idx_backtest_runs_tenant_user_created",
            "backtest_runs",
            ["tenant_id", "user_key", "created_at"],
        )


def _upgrade_postgresql() -> None:
    statements = (
        "CREATE SCHEMA IF NOT EXISTS research",
        """
        CREATE TABLE IF NOT EXISTS research.backtest_runs (
            run_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            user_key TEXT NOT NULL,
            ticker TEXT NOT NULL,
            module_key TEXT NOT NULL,
            module_version TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('completed', 'failed', 'cancelled')
            ),
            request_json JSONB NOT NULL,
            result_json JSONB NOT NULL,
            input_lineage_json JSONB NOT NULL,
            artifact_manifest_json JSONB NOT NULL DEFAULT '[]'::JSONB,
            alert_eligible BOOLEAN NOT NULL,
            retention_class TEXT NOT NULL,
            retain_until TIMESTAMPTZ NOT NULL,
            UNIQUE (tenant_id, user_key, run_id),
            CHECK (jsonb_typeof(request_json) = 'object'),
            CHECK (jsonb_typeof(result_json) = 'object'),
            CHECK (jsonb_typeof(input_lineage_json) = 'object'),
            CHECK (jsonb_typeof(artifact_manifest_json) = 'array')
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_backtest_runs_tenant_user_created
        ON research.backtest_runs (
            tenant_id, user_key, created_at DESC, run_id DESC
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_backtest_runs_tenant_ticker_created
        ON research.backtest_runs (
            tenant_id, user_key, ticker, created_at DESC, run_id DESC
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_backtest_runs_retention
        ON research.backtest_runs (tenant_id, retain_until, run_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS research.backtest_artifacts (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            user_key TEXT NOT NULL,
            run_id UUID NOT NULL,
            artifact_key TEXT NOT NULL,
            media_type TEXT NOT NULL,
            uri TEXT NOT NULL,
            sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
            created_at TIMESTAMPTZ NOT NULL,
            retain_until TIMESTAMPTZ NOT NULL,
            FOREIGN KEY (tenant_id, user_key, run_id)
                REFERENCES research.backtest_runs (
                    tenant_id, user_key, run_id
                ),
            UNIQUE (tenant_id, user_key, run_id, artifact_key)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_backtest_artifacts_retention
        ON research.backtest_artifacts (tenant_id, retain_until, id)
        """,
        """
        CREATE OR REPLACE FUNCTION research.reject_immutable_change()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable', TG_TABLE_NAME;
        END $$
        """,
        "DROP TRIGGER IF EXISTS immutable_backtest_runs "
        "ON research.backtest_runs",
        """
        CREATE TRIGGER immutable_backtest_runs
        BEFORE UPDATE ON research.backtest_runs
        FOR EACH ROW EXECUTE FUNCTION research.reject_immutable_change()
        """,
        "DROP TRIGGER IF EXISTS immutable_backtest_artifacts "
        "ON research.backtest_artifacts",
        """
        CREATE TRIGGER immutable_backtest_artifacts
        BEFORE UPDATE ON research.backtest_artifacts
        FOR EACH ROW EXECUTE FUNCTION research.reject_immutable_change()
        """,
    )
    for statement in statements:
        op.execute(statement)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _upgrade_postgresql()
    else:
        _upgrade_sqlite()


def downgrade() -> None:
    raise RuntimeError(
        "The managed research migration is destructive to downgrade. "
        "Restore the verified pre-migration backup instead."
    )
