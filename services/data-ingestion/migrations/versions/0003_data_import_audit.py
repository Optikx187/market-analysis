"""SQLite import audit, mappings, checkpoints, and deferred records.

Revision ID: 0003_data
Revises: 0002_data
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_data"
down_revision: str | Sequence[str] | None = "0002_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    statements = (
        """
        CREATE TABLE IF NOT EXISTS operations.import_runs (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            source_fingerprint JSONB NOT NULL,
            source_fingerprint_sha256 TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('dry-run', 'apply')),
            status TEXT NOT NULL CHECK (
                status IN (
                    'planned', 'running', 'interrupted', 'failed',
                    'reconciled'
                )
            ),
            options_json JSONB NOT NULL DEFAULT '{}'::JSONB,
            report_json JSONB,
            operator_id TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMPTZ,
            CHECK (jsonb_typeof(source_fingerprint) = 'object'),
            CHECK (jsonb_typeof(options_json) = 'object'),
            CHECK (
                report_json IS NULL OR jsonb_typeof(report_json) = 'object'
            )
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_import_runs_source_status
        ON operations.import_runs (
            source_fingerprint_sha256, status, started_at DESC
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS operations.import_mappings (
            id UUID PRIMARY KEY,
            first_import_run_id UUID NOT NULL
                REFERENCES operations.import_runs(id),
            source_fingerprint_sha256 TEXT NOT NULL,
            source_store TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_key TEXT NOT NULL,
            source_row_checksum TEXT NOT NULL,
            target_schema TEXT NOT NULL,
            target_table TEXT NOT NULL,
            target_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (
                source_fingerprint_sha256, source_store,
                source_table, source_key
            ),
            UNIQUE (target_schema, target_table, target_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_import_mappings_run
        ON operations.import_mappings (
            first_import_run_id, source_store, source_table, source_key
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS operations.import_table_progress (
            import_run_id UUID NOT NULL REFERENCES operations.import_runs(id),
            source_store TEXT NOT NULL,
            source_table TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'running', 'completed', 'failed')
            ),
            last_source_key TEXT,
            source_rows BIGINT NOT NULL DEFAULT 0 CHECK (source_rows >= 0),
            imported_rows BIGINT NOT NULL DEFAULT 0 CHECK (imported_rows >= 0),
            skipped_rows BIGINT NOT NULL DEFAULT 0 CHECK (skipped_rows >= 0),
            conflict_rows BIGINT NOT NULL DEFAULT 0 CHECK (conflict_rows >= 0),
            source_checksum TEXT,
            target_checksum TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (import_run_id, source_store, source_table)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS operations.import_deferred_records (
            id UUID PRIMARY KEY,
            import_run_id UUID NOT NULL REFERENCES operations.import_runs(id),
            source_fingerprint_sha256 TEXT NOT NULL,
            source_store TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_key TEXT NOT NULL,
            reason TEXT NOT NULL,
            source_row JSONB NOT NULL,
            source_row_checksum TEXT NOT NULL,
            accepted_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (
                source_fingerprint_sha256, source_store,
                source_table, source_key
            ),
            CHECK (jsonb_typeof(source_row) = 'object')
        )
        """,
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(
        "Import audit history is destructive to downgrade. "
        "Restore the verified pre-migration backup instead."
    )
