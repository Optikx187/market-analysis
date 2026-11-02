"""Durable scanner schedules, runs, leases, results, and actions.

Revision ID: 0003_research
Revises: 0002_research
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_research"
down_revision: str | Sequence[str] | None = "0002_research"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQLITE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS scanner_configs (
        tenant_id TEXT NOT NULL,
        user_key TEXT NOT NULL,
        scanner_key TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        interval_minutes INTEGER NOT NULL CHECK (interval_minutes >= 5),
        market_hours_only INTEGER NOT NULL CHECK (market_hours_only IN (0, 1)),
        next_run_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, user_key, scanner_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scanner_runs (
        run_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_key TEXT NOT NULL,
        scanner_key TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        trigger_type TEXT NOT NULL
            CHECK (trigger_type IN ('scheduled', 'manual')),
        status TEXT NOT NULL
            CHECK (status IN ('queued', 'running', 'completed', 'failed')),
        scheduled_for TEXT NOT NULL,
        available_at TEXT NOT NULL,
        lease_owner TEXT,
        lease_expires_at TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        result_json TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE (tenant_id, user_key, scanner_key, idempotency_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_runs_claim
    ON scanner_runs (
        tenant_id, user_key, scanner_key, status,
        available_at, lease_expires_at, scheduled_for
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scanner_opportunity_actions (
        tenant_id TEXT NOT NULL,
        user_key TEXT NOT NULL,
        opportunity_id TEXT NOT NULL,
        action TEXT NOT NULL
            CHECK (action IN ('approve', 'reject', 'snooze', 'edit')),
        state_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, user_key, opportunity_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scanner_notification_receipts (
        tenant_id TEXT NOT NULL,
        user_key TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES scanner_runs(run_id),
        opportunity_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, user_key, idempotency_key)
    )
    """,
)

POSTGRESQL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS research.scanner_configs (
        tenant_id UUID NOT NULL,
        user_key TEXT NOT NULL,
        scanner_key TEXT NOT NULL,
        enabled BOOLEAN NOT NULL,
        interval_minutes INTEGER NOT NULL CHECK (interval_minutes >= 5),
        market_hours_only BOOLEAN NOT NULL,
        next_run_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (tenant_id, user_key, scanner_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.scanner_runs (
        run_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        user_key TEXT NOT NULL,
        scanner_key TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        trigger_type TEXT NOT NULL
            CHECK (trigger_type IN ('scheduled', 'manual')),
        status TEXT NOT NULL
            CHECK (status IN ('queued', 'running', 'completed', 'failed')),
        scheduled_for TIMESTAMPTZ NOT NULL,
        available_at TIMESTAMPTZ NOT NULL,
        lease_owner TEXT,
        lease_expires_at TIMESTAMPTZ,
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        result_json JSONB,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL,
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (tenant_id, user_key, scanner_key, idempotency_key),
        CHECK (result_json IS NULL OR jsonb_typeof(result_json) = 'object')
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_runs_claim
    ON research.scanner_runs (
        tenant_id, user_key, scanner_key, status,
        available_at, lease_expires_at, scheduled_for
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.scanner_opportunity_actions (
        tenant_id UUID NOT NULL,
        user_key TEXT NOT NULL,
        opportunity_id TEXT NOT NULL,
        action TEXT NOT NULL
            CHECK (action IN ('approve', 'reject', 'snooze', 'edit')),
        state_json JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (tenant_id, user_key, opportunity_id),
        CHECK (jsonb_typeof(state_json) = 'object')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.scanner_notification_receipts (
        tenant_id UUID NOT NULL,
        user_key TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        run_id UUID NOT NULL REFERENCES research.scanner_runs(run_id),
        opportunity_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (tenant_id, user_key, idempotency_key)
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
        "The durable scanner migration is destructive to downgrade. "
        "Restore the verified pre-migration backup instead."
    )
