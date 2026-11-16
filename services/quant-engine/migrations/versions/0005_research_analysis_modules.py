"""Versioned analysis module registry, selections, and runs.

Revision ID: 0005_research
Revises: 0004_research
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_research"
down_revision: str | Sequence[str] | None = "0004_research"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQLITE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS analysis_module_definitions (
        module_key TEXT NOT NULL,
        module_version TEXT NOT NULL,
        definition_json TEXT NOT NULL,
        definition_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (module_key, module_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_module_selections (
        tenant_id TEXT NOT NULL,
        user_key TEXT NOT NULL,
        module_key TEXT NOT NULL,
        module_version TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        config_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, user_key, module_key),
        FOREIGN KEY (module_key, module_version)
            REFERENCES analysis_module_definitions (
                module_key, module_version
            )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_module_runs (
        run_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_key TEXT NOT NULL,
        module_key TEXT NOT NULL,
        module_version TEXT NOT NULL,
        ticker TEXT NOT NULL,
        asset_type TEXT NOT NULL,
        horizon TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
        request_json TEXT NOT NULL,
        result_json TEXT,
        evidence_json TEXT NOT NULL,
        lineage_json TEXT NOT NULL,
        normalized_event_json TEXT,
        error TEXT,
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY (module_key, module_version)
            REFERENCES analysis_module_definitions (
                module_key, module_version
            )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_analysis_module_runs_user_created
    ON analysis_module_runs (
        tenant_id, user_key, module_key, completed_at DESC
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS immutable_analysis_module_definitions_update
    BEFORE UPDATE ON analysis_module_definitions
    BEGIN
        SELECT RAISE(ABORT, 'analysis_module_definitions is immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS immutable_analysis_module_definitions_delete
    BEFORE DELETE ON analysis_module_definitions
    BEGIN
        SELECT RAISE(ABORT, 'analysis_module_definitions is immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS immutable_analysis_module_runs_update
    BEFORE UPDATE ON analysis_module_runs
    BEGIN
        SELECT RAISE(ABORT, 'analysis_module_runs is immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS immutable_analysis_module_runs_delete
    BEFORE DELETE ON analysis_module_runs
    BEGIN
        SELECT RAISE(ABORT, 'analysis_module_runs is immutable');
    END
    """,
)

POSTGRESQL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS research.analysis_module_definitions (
        module_key TEXT NOT NULL,
        module_version TEXT NOT NULL,
        definition_json JSONB NOT NULL,
        definition_sha256 TEXT NOT NULL
            CHECK (definition_sha256 ~ '^[0-9a-f]{64}$'),
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (module_key, module_version),
        CHECK (jsonb_typeof(definition_json) = 'object')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.analysis_module_selections (
        tenant_id UUID NOT NULL,
        user_key TEXT NOT NULL,
        module_key TEXT NOT NULL,
        module_version TEXT NOT NULL,
        enabled BOOLEAN NOT NULL,
        config_json JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (tenant_id, user_key, module_key),
        FOREIGN KEY (module_key, module_version)
            REFERENCES research.analysis_module_definitions (
                module_key, module_version
            ),
        CHECK (jsonb_typeof(config_json) = 'object')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.analysis_module_runs (
        run_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        user_key TEXT NOT NULL,
        module_key TEXT NOT NULL,
        module_version TEXT NOT NULL,
        ticker TEXT NOT NULL,
        asset_type TEXT NOT NULL,
        horizon TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
        request_json JSONB NOT NULL,
        result_json JSONB,
        evidence_json JSONB NOT NULL,
        lineage_json JSONB NOT NULL,
        normalized_event_json JSONB,
        error TEXT,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        FOREIGN KEY (module_key, module_version)
            REFERENCES research.analysis_module_definitions (
                module_key, module_version
            ),
        CHECK (jsonb_typeof(request_json) = 'object'),
        CHECK (result_json IS NULL OR jsonb_typeof(result_json) = 'object'),
        CHECK (jsonb_typeof(evidence_json) = 'array'),
        CHECK (jsonb_typeof(lineage_json) = 'object'),
        CHECK (
            normalized_event_json IS NULL
            OR jsonb_typeof(normalized_event_json) = 'object'
        )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_analysis_module_runs_user_created
    ON research.analysis_module_runs (
        tenant_id, user_key, module_key, completed_at DESC
    )
    """,
    """
    DROP TRIGGER IF EXISTS immutable_analysis_module_definitions
    ON research.analysis_module_definitions
    """,
    """
    CREATE TRIGGER immutable_analysis_module_definitions
    BEFORE UPDATE OR DELETE ON research.analysis_module_definitions
    FOR EACH ROW EXECUTE FUNCTION research.reject_immutable_change()
    """,
    """
    DROP TRIGGER IF EXISTS immutable_analysis_module_runs
    ON research.analysis_module_runs
    """,
    """
    CREATE TRIGGER immutable_analysis_module_runs
    BEFORE UPDATE OR DELETE ON research.analysis_module_runs
    FOR EACH ROW EXECUTE FUNCTION research.reject_immutable_change()
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
        "Analysis module evidence is destructive to downgrade. "
        "Restore the verified pre-migration backup instead."
    )
