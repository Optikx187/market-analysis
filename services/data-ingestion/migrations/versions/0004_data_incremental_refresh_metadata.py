"""SQLite incremental-refresh provider metadata.

Revision ID: 0004_data
Revises: 0003_data
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0004_data"
down_revision: str | Sequence[str] | None = "0003_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("candles")
    }
    additions = (
        ("provider", sa.String(length=30), "'legacy'"),
        ("provider_symbol", sa.String(length=50), "''"),
        ("provider_observed_at", sa.DateTime(), None),
        ("first_ingested_at", sa.DateTime(), "CURRENT_TIMESTAMP"),
        ("ingested_at", sa.DateTime(), "CURRENT_TIMESTAMP"),
        ("provider_revision", sa.Integer(), "1"),
        ("content_checksum", sa.String(length=64), None),
    )
    with op.batch_alter_table("candles") as batch:
        for name, column_type, default in additions:
            if name not in columns:
                batch.add_column(
                    sa.Column(
                        name,
                        column_type,
                        nullable=name in {
                            "provider_observed_at",
                            "content_checksum",
                        },
                        server_default=sa.text(default) if default else None,
                    )
                )
    op.create_table(
        "candle_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("candle_id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("interval", sa.String(length=10), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("provider_symbol", sa.String(length=50), nullable=False),
        sa.Column("provider_observed_at", sa.DateTime()),
        sa.Column("ingested_at", sa.DateTime()),
        sa.Column("provider_revision", sa.Integer(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("content_checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "superseded_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_candle_revisions_candle_id",
        "candle_revisions",
        ["candle_id"],
    )
    op.create_index(
        "ix_candle_revisions_ticker",
        "candle_revisions",
        ["ticker"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Incremental-refresh lineage is destructive to downgrade. "
        "Restore the verified pre-migration backup instead."
    )
