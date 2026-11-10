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


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("SCANNER_WORKER_POLL_SECONDS", 0, "greater than zero"),
        ("SCANNER_LEASE_SECONDS", 2, "at least 3 seconds"),
        ("SCANNER_MAX_ATTEMPTS", 0, "at least 1"),
        ("SCANNER_RETRY_SECONDS", -1, "must not be negative"),
        (
            "SCANNER_WORKER_STALE_SECONDS",
            5,
            "must exceed the poll interval",
        ),
        ("SCANNER_LAG_ALERT_SECONDS", 0, "must be at least 1"),
        ("SCANNER_DURATION_ALERT_SECONDS", 0, "must be at least 1"),
        (
            "SCANNER_ERROR_RATE_ALERT_PCT",
            101,
            "at most 100",
        ),
        (
            "SCANNER_OBSERVABILITY_WINDOW_MINUTES",
            0,
            "must be at least 1",
        ),
        (
            "EQUITY_EXCHANGE_CALENDAR",
            "NOT_A_CALENDAR",
            "not a known exchange calendar",
        ),
    ],
)
def test_scanner_worker_settings_fail_closed(
    name: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **{name: value})
