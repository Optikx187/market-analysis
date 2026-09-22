import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import auth, main
from app.config import settings
from app.database import Base
from app.models import User


STRONG_SECRET = "auth-security-regression-secret-32-bytes-long"


def test_new_password_hashes_are_salted_scrypt() -> None:
    first = auth.hash_password("correct horse battery staple")
    second = auth.hash_password("correct horse battery staple")

    assert first.startswith("scrypt$")
    assert first != second
    assert auth.verify_password("correct horse battery staple", first)
    assert not auth.verify_password("wrong password", first)
    assert not auth.password_needs_rehash(first)


def test_legacy_sha256_hashes_remain_verifiable_and_require_rehash() -> None:
    legacy = hashlib.sha256(b"legacy-password").hexdigest()

    assert auth.verify_password("legacy-password", legacy)
    assert not auth.verify_password("wrong-password", legacy)
    assert auth.password_needs_rehash(legacy)


@pytest.mark.parametrize(
    "secret",
    ["", auth.DEFAULT_JWT_SECRET, "short-secret", "a" * 64],
)
def test_auth_configuration_rejects_missing_default_or_weak_secrets(
    monkeypatch: pytest.MonkeyPatch,
    secret: str,
) -> None:
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "JWT_SECRET", secret)
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")

    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth.validate_auth_configuration()


def test_auth_enabled_startup_fails_closed_with_default_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "JWT_SECRET", auth.DEFAULT_JWT_SECRET)
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")

    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        with TestClient(main.app):
            pass


def test_auth_configuration_allows_disabled_auth_and_strong_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "JWT_SECRET", auth.DEFAULT_JWT_SECRET)
    auth.validate_auth_configuration()

    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "JWT_SECRET", STRONG_SECRET)
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    auth.validate_auth_configuration()


def test_tokens_reject_tampering_expiration_and_invalid_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET", STRONG_SECRET)
    monkeypatch.setattr(settings, "JWT_EXPIRY_HOURS", 1)
    monkeypatch.setattr(auth.time, "time", lambda: 1_000.0)
    token = auth.create_token("42", "alice")

    assert auth.decode_token(token)["sub"] == "42"

    header, payload, signature = token.split(".")
    tampered_payload = auth._b64url_encode(b'{"sub":"7","username":"alice","exp":4600}')
    with pytest.raises(ValueError, match="signature"):
        auth.decode_token(f"{header}.{tampered_payload}.{signature}")

    monkeypatch.setattr(auth.time, "time", lambda: 5_000.0)
    with pytest.raises(ValueError, match="expired"):
        auth.decode_token(token)

    monkeypatch.setattr(auth.time, "time", lambda: 1_000.0)
    invalid_claims = auth._b64url_encode(b'{"sub":"","username":"alice","exp":4600}')
    signing_input = f"{header}.{invalid_claims}".encode()
    invalid_signature = auth._b64url_encode(
        auth.hmac.new(STRONG_SECRET.encode(), signing_input, auth.hashlib.sha256).digest()
    )
    with pytest.raises(ValueError, match="subject"):
        auth.decode_token(f"{header}.{invalid_claims}.{invalid_signature}")


def test_tokens_reject_algorithm_confusion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET", STRONG_SECRET)
    header = auth._b64url_encode(b'{"alg":"none","typ":"JWT"}')
    payload = auth._b64url_encode(b'{"sub":"42","username":"alice","exp":4102444800}')
    signing_input = f"{header}.{payload}".encode()
    signature = auth._b64url_encode(
        auth.hmac.new(STRONG_SECRET.encode(), signing_input, auth.hashlib.sha256).digest()
    )

    with pytest.raises(ValueError, match="header"):
        auth.decode_token(f"{header}.{payload}.{signature}")


def test_successful_login_migrates_legacy_password_hash(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> tuple[str, dict[str, object]]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy-login.db'}")
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        legacy_hash = hashlib.sha256(b"legacy-password").hexdigest()
        async with session_factory() as session:
            session.add(
                User(
                    username="legacy-user",
                    password_hash=legacy_hash,
                    display_name="Legacy User",
                )
            )
            await session.commit()

        async with session_factory() as session:
            response = await main.login(
                main.LoginRequest(username="legacy-user", password="legacy-password"),
                session,
            )

        async with session_factory() as session:
            migrated_hash = (
                await session.execute(select(User.password_hash).where(User.username == "legacy-user"))
            ).scalar_one()
        await engine.dispose()
        return migrated_hash, response

    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "JWT_SECRET", STRONG_SECRET)
    migrated_hash, response = asyncio.run(exercise())

    assert migrated_hash.startswith("scrypt$")
    assert migrated_hash != hashlib.sha256(b"legacy-password").hexdigest()
    assert auth.verify_password("legacy-password", migrated_hash)
    assert response["username"] == "legacy-user"
    assert auth.decode_token(str(response["token"]))["sub"] == str(response["user_id"])
