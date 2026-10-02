import asyncio
import logging

import httpx

from app import credentials


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "ALPACA_API_KEY": "fake-key",
            "ALPACA_API_SECRET": "fake-secret",
            "EMPTY": "",
            "INVALID": 123,
        }


class FakeClient:
    def __init__(self, captured: dict[str, object], *, timeout: int):
        captured["timeout"] = timeout
        self.captured = captured

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, *, headers: dict[str, str]) -> FakeResponse:
        self.captured["url"] = url
        self.captured["headers"] = headers
        return FakeResponse()


def test_provider_credentials_use_authenticated_internal_endpoint(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    credentials._cache.clear()
    monkeypatch.setattr(
        credentials.httpx,
        "AsyncClient",
        lambda *, timeout: FakeClient(captured, timeout=timeout),
    )
    monkeypatch.setattr(
        credentials.settings,
        "PORTFOLIO_ENGINE_URL",
        "http://portfolio-engine:8002",
    )
    monkeypatch.setattr(
        credentials.settings,
        "INTERNAL_SERVICE_TOKEN",
        "fake-internal-token",
    )

    result = asyncio.run(credentials.get_provider_credentials(" ALPACA "))

    assert result == {
        "ALPACA_API_KEY": "fake-key",
        "ALPACA_API_SECRET": "fake-secret",
    }
    assert captured == {
        "timeout": 5,
        "url": "http://portfolio-engine:8002/internal/credentials/alpaca",
        "headers": {"X-Internal-Service-Token": "fake-internal-token"},
    }


def test_provider_credentials_fail_closed_without_internal_token(
    monkeypatch,
) -> None:
    credentials._cache.clear()
    monkeypatch.setattr(credentials.settings, "INTERNAL_SERVICE_TOKEN", "")

    result = asyncio.run(credentials.get_provider_credentials("alpaca"))

    assert result == {}


def test_provider_credential_errors_do_not_log_request_secrets(
    monkeypatch,
    caplog,
) -> None:
    class FailingClient:
        async def __aenter__(self) -> "FailingClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, *, headers: dict[str, str]) -> FakeResponse:
            request = httpx.Request("GET", f"{url}?secret=fake-leak-value")
            raise httpx.RequestError("fake-leak-value", request=request)

    credentials._cache.clear()
    monkeypatch.setattr(
        credentials.httpx,
        "AsyncClient",
        lambda *, timeout: FailingClient(),
    )
    monkeypatch.setattr(
        credentials.settings,
        "INTERNAL_SERVICE_TOKEN",
        "fake-internal-token",
    )

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(credentials.get_provider_credentials("alpaca"))

    assert result == {}
    assert "fake-leak-value" not in caplog.text
    assert "RequestError" in caplog.text
