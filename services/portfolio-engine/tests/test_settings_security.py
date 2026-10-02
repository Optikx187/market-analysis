import asyncio
import base64
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import main
from app.config import settings
from app.credential_store import CredentialCipher, CredentialDecryptionError
from app.database import Base, get_db
from app.models import CredentialSecret


OPERATOR_TOKEN = "settings-test-operator"
FAKE_SECRET = "fake-existing-secret"
REPLACEMENT_SECRET = "fake-replacement-secret"
ENCRYPTION_KEY = "V-QWAXZPbskGCTNyqzZW5lDahUh6RLa-wRezwi46oXg="
ROTATED_KEY = "61yG-b591NSqiHkZm4m1iDogq5LRI3IphK7DQKpSulo="
INTERNAL_TOKEN = "credential-test-internal-service-token"


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
    for key_group in main.PROVIDER_KEYS.values():
        for key in key_group:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOST_ENV_PATH", str(env_path))
    monkeypatch.setattr(settings, "SETTINGS_OPERATOR_TOKEN", OPERATOR_TOKEN)
    monkeypatch.setattr(settings, "CREDENTIAL_ENCRYPTION_KEYS", ENCRYPTION_KEY)
    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", INTERNAL_TOKEN)
    monkeypatch.setattr(main, "async_session", session_factory)
    asyncio.run(main._initialize_credential_store())
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
    assert FAKE_SECRET not in persisted
    assert REPLACEMENT_SECRET not in persisted
    assert "ALPACA_API_KEY" not in persisted
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


def test_database_contains_authenticated_ciphertext_not_plaintext_or_legacy_base64(
    protected_settings_client: tuple[TestClient, Path],
) -> None:
    client, env_path = protected_settings_client
    response = client.post(
        "/api/settings/credentials/save",
        headers=_operator_headers(),
        json={"credentials": {"ALPACA_API_KEY": REPLACEMENT_SECRET}, "overwrite": True},
    )
    assert response.status_code == 200

    database_bytes = (env_path.parent / "portfolio-test.db").read_bytes()
    assert REPLACEMENT_SECRET.encode() not in database_bytes
    assert base64.urlsafe_b64encode(REPLACEMENT_SECRET.encode()) not in database_bytes
    assert b"fernet$" in database_bytes


def test_existing_encrypted_database_value_wins_over_stale_env_copy(
    protected_settings_client: tuple[TestClient, Path],
) -> None:
    client, env_path = protected_settings_client
    env_path.write_text(f"ALPACA_API_KEY={REPLACEMENT_SECRET}\nMAX_DRAWDOWN_PCT=0.12\n")

    asyncio.run(main._initialize_credential_store())

    response = client.get(
        "/internal/credentials/alpaca",
        headers={"X-Internal-Service-Token": INTERNAL_TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["ALPACA_API_KEY"] == FAKE_SECRET
    assert REPLACEMENT_SECRET not in env_path.read_text()


def test_credential_cipher_rotates_old_key_and_rejects_unknown_key() -> None:
    old_cipher = CredentialCipher(ENCRYPTION_KEY)
    stored = old_cipher.encrypt(FAKE_SECRET)

    rotating_cipher = CredentialCipher(f"{ROTATED_KEY},{ENCRYPTION_KEY}")
    decrypted = rotating_cipher.decrypt(stored)
    assert decrypted.value == FAKE_SECRET
    assert decrypted.needs_rotation is True

    rotated = rotating_cipher.encrypt(decrypted.value)
    assert CredentialCipher(ROTATED_KEY).decrypt(rotated).value == FAKE_SECRET
    with pytest.raises(CredentialDecryptionError):
        CredentialCipher(ENCRYPTION_KEY).decrypt(rotated)


def test_startup_reencrypts_rows_with_new_primary_key(
    protected_settings_client: tuple[TestClient, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _ = protected_settings_client
    monkeypatch.setattr(
        settings,
        "CREDENTIAL_ENCRYPTION_KEYS",
        f"{ROTATED_KEY},{ENCRYPTION_KEY}",
    )

    asyncio.run(main._initialize_credential_store())

    async def stored_value() -> str:
        async with main.async_session() as db:
            return (
                await db.execute(
                    select(CredentialSecret.value)
                    .where(CredentialSecret.key == "ALPACA_API_KEY")
                )
            ).scalar_one()

    rotated = asyncio.run(stored_value())
    assert CredentialCipher(ROTATED_KEY).decrypt(rotated).value == FAKE_SECRET
    with pytest.raises(CredentialDecryptionError):
        CredentialCipher(ENCRYPTION_KEY).decrypt(rotated)


def test_legacy_base64_rows_are_migrated_and_internal_access_is_token_protected(
    protected_settings_client: tuple[TestClient, Path],
) -> None:
    client, _ = protected_settings_client
    legacy_value = base64.urlsafe_b64encode(b"legacy-telegram-secret").decode()

    async def replace_with_legacy() -> None:
        async with main.async_session() as db:
            row = CredentialSecret(
                provider="telegram",
                key="TELEGRAM_BOT_TOKEN",
                value=legacy_value,
                verified=True,
            )
            db.add(row)
            await db.commit()

    asyncio.run(replace_with_legacy())

    missing = client.get("/internal/credentials/telegram")
    invalid = client.get(
        "/internal/credentials/telegram",
        headers={"X-Internal-Service-Token": "wrong"},
    )
    authorized = client.get(
        "/internal/credentials/telegram",
        headers={"X-Internal-Service-Token": INTERNAL_TOKEN},
    )
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json() == {"TELEGRAM_BOT_TOKEN": "legacy-telegram-secret"}

    async def stored_value() -> str:
        async with main.async_session() as db:
            return (
                await db.execute(
                    select(CredentialSecret.value)
                    .where(CredentialSecret.key == "TELEGRAM_BOT_TOKEN")
                )
            ).scalar_one()

    migrated = asyncio.run(stored_value())
    assert migrated.startswith("fernet$")
    assert legacy_value not in migrated


def test_credential_save_fails_closed_without_external_keys(
    protected_settings_client: tuple[TestClient, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = protected_settings_client
    monkeypatch.setattr(settings, "CREDENTIAL_ENCRYPTION_KEYS", "")

    response = client.post(
        "/api/settings/credentials/save",
        headers=_operator_headers(),
        json={"credentials": {"ALPACA_API_KEY": REPLACEMENT_SECRET}},
    )

    assert response.status_code == 503
    assert REPLACEMENT_SECRET not in response.text
