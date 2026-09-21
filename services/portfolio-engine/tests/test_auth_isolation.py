import asyncio
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import main
from app.config import settings
from app.database import Base, _migrate_existing_tables, get_db


PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/status",
}


@pytest.fixture
def auth_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    database_path = tmp_path / "auth-isolation.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(_migrate_existing_tables)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    asyncio.run(create_tables())
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "JWT_SECRET", "auth-isolation-test-secret")
    monkeypatch.setattr(settings, "SETTINGS_OPERATOR_TOKEN", "settings-operator-test-token")
    main.app.dependency_overrides[get_db] = override_get_db
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def _register(client: TestClient, username: str) -> tuple[str, str]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": f"{username}-password"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return str(payload["user_id"]), payload["token"]


def _headers(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def _manual_trade(ticker: str) -> dict[str, object]:
    return {
        "ticker": ticker,
        "direction": "BUY",
        "entry_price": 100.0,
        "quantity": 1.0,
        "stop_loss": 95.0,
        "target_price": 115.0,
    }


def test_auth_boundary_denies_every_non_public_api_route(auth_client: TestClient) -> None:
    checked: set[tuple[str, str]] = set()
    for route in main.app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        if route.path in PUBLIC_PATHS:
            continue
        path = re.sub(r"\{[^}]+\}", "1", route.path)
        for method in route.methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            response = auth_client.request(method, path)
            assert response.status_code == 401, f"{method} {route.path}: {response.status_code} {response.text}"
            checked.add((method, route.path))

    assert len(checked) >= 55


def test_public_auth_routes_remain_available(auth_client: TestClient) -> None:
    assert auth_client.get("/health").status_code == 200
    assert auth_client.get("/api/auth/status").json() == {"auth_enabled": True}

    _, token = _register(auth_client, "public-route-user")
    login = auth_client.post(
        "/api/auth/login",
        json={"username": "public-route-user", "password": "public-route-user-password"},
    )
    assert login.status_code == 200
    assert login.json()["token"] == token


def test_portfolios_trades_and_paper_orders_are_isolated(auth_client: TestClient) -> None:
    alice_id, alice_token = _register(auth_client, "alice")
    bob_id, bob_token = _register(auth_client, "bob")
    alice_headers = _headers(alice_token)
    bob_headers = _headers(bob_token)

    assert auth_client.get("/api/auth/session", headers=alice_headers).json() == {
        "auth_enabled": True,
        "user_id": alice_id,
    }
    assert auth_client.get("/api/auth/session", headers=bob_headers).json() == {
        "auth_enabled": True,
        "user_id": bob_id,
    }

    alice_trade = auth_client.post(
        "/api/trades/manual",
        headers=alice_headers,
        json=_manual_trade("AAPL"),
    )
    assert alice_trade.status_code == 200, alice_trade.text
    alice_trade_id = alice_trade.json()["id"]

    alice_order = auth_client.post(
        "/api/paper-orders",
        headers=alice_headers,
        json={
            "idempotency_key": "alice-order",
            "ticker": "MSFT",
            "side": "BUY",
            "order_type": "market",
            "quantity": 1,
            "reference_price": 50,
        },
    )
    assert alice_order.status_code == 201, alice_order.text
    alice_order_id = alice_order.json()["id"]

    assert [trade["ticker"] for trade in auth_client.get("/api/trades", headers=alice_headers).json()] == ["AAPL"]
    assert auth_client.get("/api/trades", headers=bob_headers).json() == []
    assert auth_client.post(
        f"/api/trades/{alice_trade_id}/close",
        headers=bob_headers,
        json={"exit_price": 110},
    ).status_code == 404

    assert len(auth_client.get("/api/paper-orders", headers=alice_headers).json()["orders"]) == 1
    assert auth_client.get("/api/paper-orders", headers=bob_headers).json()["orders"] == []
    assert auth_client.get(f"/api/paper-orders/{alice_order_id}", headers=bob_headers).status_code == 404

    alice_portfolio = auth_client.get("/api/portfolio", headers=alice_headers).json()
    bob_portfolio = auth_client.get("/api/portfolio", headers=bob_headers).json()
    assert alice_portfolio["balance"] == 9850.0
    assert bob_portfolio["balance"] == settings.INITIAL_BALANCE

    bob_trade = auth_client.post(
        "/api/trades/manual",
        headers=bob_headers,
        json=_manual_trade("NVDA"),
    )
    assert bob_trade.status_code == 200, bob_trade.text
    assert [trade["ticker"] for trade in auth_client.get("/api/trades", headers=alice_headers).json()] == ["AAPL"]
    assert [trade["ticker"] for trade in auth_client.get("/api/trades", headers=bob_headers).json()] == ["NVDA"]
    assert alice_id != bob_id


def test_operator_tokens_remain_additional_security_boundaries(auth_client: TestClient) -> None:
    _, user_token = _register(auth_client, "operator-user")
    jwt_headers = _headers(user_token)
    operator_headers = {"X-Settings-Operator-Token": "settings-operator-test-token"}

    assert auth_client.get("/api/settings/env", headers=operator_headers).status_code == 401
    assert auth_client.get("/api/settings/env", headers=jwt_headers).status_code == 401
    assert auth_client.get(
        "/api/settings/env",
        headers={**jwt_headers, **operator_headers},
    ).status_code == 200


def test_auth_disabled_uses_the_default_single_user_scope(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    assert auth_client.get("/api/auth/session").json() == {
        "auth_enabled": False,
        "user_id": "default",
    }
    created = auth_client.post("/api/trades/manual", json=_manual_trade("SPY"))
    assert created.status_code == 200, created.text
    assert [trade["ticker"] for trade in auth_client.get("/api/trades").json()] == ["SPY"]
