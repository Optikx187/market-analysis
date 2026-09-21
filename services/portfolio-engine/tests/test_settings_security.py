import asyncio
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import main
from app.config import settings
from app.database import Base, get_db


OPERATOR_TOKEN = "settings-test-operator"
FAKE_SECRET = "fake-existing-secret"
REPLACEMENT_SECRET = "fake-replacement-secret"


@pytest.fixture
def protected_settings_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Path]]:
    database_path = tmp_path / "portfolio-test.db"
    env_path = tmp_path / ".env"
    env_path.write_text(f"ALPACA_API_KEY={FAKE_SECRET}\nMAX_DRAWDOWN_PCT=0.12\n")
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    asyncio.run(create_tables())
    monkeypatch.setenv("HOST_ENV_PATH", str(env_path))
    monkeypatch.setattr(settings, "SETTINGS_OPERATOR_TOKEN", OPERATOR_TOKEN)
    main.app.dependency_overrides[get_db] = override_get_db
    yield TestClient(main.app), env_path
    main.app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def _operator_headers(token: str = OPERATOR_TOKEN) -> dict[str, str]:
    return {"X-Settings-Operator-Token": token}


def test_sensitive_settings_fail_closed_without_configured_operator_token(
    protected_settings_client: tuple[TestClient, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = protected_settings_client
    monkeypatch.setattr(settings, "SETTINGS_OPERATOR_TOKEN", "")

    requests = [
        client.get("/api/settings/credentials"),
        client.get("/api/settings/credentials/all"),
        client.post(
            "/api/settings/credentials/save",
            json={"credentials": {"ALPACA_API_KEY": REPLACEMENT_SECRET}},
        ),
        client.get("/api/settings/env"),
        client.post("/api/settings/env", json={"key": "MAX_DRAWDOWN_PCT", "value": 0.2}),
    ]

    assert {response.status_code for response in requests} == {403}


def test_sensitive_settings_reject_missing_and_invalid_operator_tokens(
    protected_settings_client: tuple[TestClient, Path],
) -> None:
    client, _ = protected_settings_client

    missing = client.get("/api/settings/credentials/all")
    invalid = client.post(
        "/api/settings/env",
        headers=_operator_headers("wrong-token"),
        json={"key": "MAX_DRAWDOWN_PCT", "value": 0.2},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_authorized_operator_can_manage_masked_credentials_and_risk_settings(
    protected_settings_client: tuple[TestClient, Path],
) -> None:
    client, env_path = protected_settings_client
    headers = _operator_headers()

    status_response = client.get("/api/settings/credentials/all", headers=headers)
    assert status_response.status_code == 200
    assert FAKE_SECRET not in status_response.text
    assert status_response.json()["alpaca"]["masked"]["ALPACA_API_KEY"] != FAKE_SECRET

    save_response = client.post(
        "/api/settings/credentials/save",
        headers=headers,
        json={
            "credentials": {"ALPACA_API_KEY": REPLACEMENT_SECRET},
            "overwrite": True,
        },
    )
    assert save_response.status_code == 200
    assert REPLACEMENT_SECRET not in save_response.text

    update_response = client.post(
        "/api/settings/env",
        headers=headers,
        json={"key": "MAX_DRAWDOWN_PCT", "value": 0.2},
    )
    assert update_response.status_code == 200

    settings_response = client.get("/api/settings/env", headers=headers)
    assert settings_response.status_code == 200
    assert settings_response.json()["MAX_DRAWDOWN_PCT"]["value"] == 0.2

    persisted = env_path.read_text()
    assert f"ALPACA_API_KEY={REPLACEMENT_SECRET}" in persisted
    assert "MAX_DRAWDOWN_PCT=0.2" in persisted
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_plaintext_reveal_and_environment_debug_routes_are_removed(
    protected_settings_client: tuple[TestClient, Path],
) -> None:
    client, _ = protected_settings_client
    headers = _operator_headers()

    reveal = client.post(
        "/api/settings/credentials/reveal",
        headers=headers,
        json={"key": "ALPACA_API_KEY"},
    )
    debug = client.get("/api/settings/env-debug", headers=headers)

    assert reveal.status_code == 404
    assert debug.status_code == 404
