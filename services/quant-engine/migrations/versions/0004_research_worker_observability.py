"""Durable scanner worker health and observability.

Revision ID: 0004_research
Revises: 0003_research
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_research"
down_revision: str | Sequence[str] | None = "0003_research"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQLITE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS scanner_worker_heartbeats (
        tenant_id TEXT NOT NULL,
        user_key TEXT NOT NULL,
        worker_id TEXT NOT NULL,
        hostname TEXT NOT NULL,
        process_id INTEGER NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('idle', 'running', 'error', 'stopped')),
        started_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        current_run_id TEXT,
        current_scanner_key TEXT,
        completed_runs INTEGER NOT NULL DEFAULT 0
            CHECK (completed_runs >= 0),
        failed_runs INTEGER NOT NULL DEFAULT 0
            CHECK (failed_runs >= 0),
        last_run_duration_ms INTEGER,
        last_error TEXT,
        PRIMARY KEY (tenant_id, user_key, worker_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_worker_heartbeats_seen
    ON scanner_worker_heartbeats (
        tenant_id, user_key, last_seen_at DESC
    )
    """,
)

POSTGRESQL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS research.scanner_worker_heartbeats (
        tenant_id UUID NOT NULL,
        user_key TEXT NOT NULL,
        worker_id TEXT NOT NULL,
        hostname TEXT NOT NULL,
        process_id INTEGER NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('idle', 'running', 'error', 'stopped')),
        started_at TIMESTAMPTZ NOT NULL,
        last_seen_at TIMESTAMPTZ NOT NULL,
        current_run_id UUID,
        current_scanner_key TEXT,
        completed_runs BIGINT NOT NULL DEFAULT 0
            CHECK (completed_runs >= 0),
        failed_runs BIGINT NOT NULL DEFAULT 0
            CHECK (failed_runs >= 0),
        last_run_duration_ms BIGINT,
        last_error TEXT,
        PRIMARY KEY (tenant_id, user_key, worker_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_worker_heartbeats_seen
    ON research.scanner_worker_heartbeats (
        tenant_id, user_key, last_seen_at DESC
    )
    """,
)


def upgrade() -> None:
    statements = (
        POSTGRESQL_STATEMENTS
        if op.get_bind().dialect.name == "postgresql"
        else SQLITE_STATEMENTS
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(
        "Worker health history is destructive to downgrade. "
        "Restore the verified pre-migration backup instead."
    )
