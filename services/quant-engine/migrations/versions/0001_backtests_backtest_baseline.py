"""Backtest history baseline.

Revision ID: 0001_backtests
Revises:
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0001_backtests"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "backtest_runs" not in inspector.get_table_names():
        op.create_table(
            "backtest_runs",
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("ticker", sa.Text(), nullable=False),
            sa.Column("strategy_version", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("request_json", sa.Text(), nullable=False),
            sa.Column("result_json", sa.Text(), nullable=False),
            sa.Column("alert_eligible", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("run_id"),
        )
    inspector = inspect(op.get_bind())
    indexes = {
        index["name"] for index in inspector.get_indexes("backtest_runs")
    }
    if "idx_backtest_runs_ticker_created" not in indexes:
        op.execute(
            "CREATE INDEX idx_backtest_runs_ticker_created "
            "ON backtest_runs(ticker, created_at DESC)"
        )


def downgrade() -> None:
    raise RuntimeError(
        "The backtest baseline is destructive to downgrade. "
        "Restore the verified pre-migration backup instead."
    )
