import pytest
from pydantic import ValidationError

from app.config import Settings


def test_postgresql_research_url_uses_psycopg_driver() -> None:
    settings = Settings(
        _env_file=None,
        RESEARCH_STORAGE_BACKEND="postgresql",
        RESEARCH_DATABASE_URL="postgresql://user:pass@db/research",
    )

    assert settings.research_database_url == (
        "postgresql+psycopg://user:pass@db/research"
    )


def test_research_backend_and_database_url_must_match() -> None:
    with pytest.raises(ValidationError, match="requires a PostgreSQL"):
        Settings(
            _env_file=None,
            RESEARCH_STORAGE_BACKEND="postgresql",
            RESEARCH_DATABASE_URL="sqlite:////tmp/research.db",
        )

    with pytest.raises(ValidationError, match="requires a SQLite"):
        Settings(
            _env_file=None,
            RESEARCH_STORAGE_BACKEND="sqlite-transition",
            RESEARCH_DATABASE_URL=(
                "postgresql+psycopg://user:pass@db/research"
            ),
        )
