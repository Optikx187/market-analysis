import pytest
from pydantic import ValidationError

from app.config import Settings


SANDBOX_ENDPOINTS = {
    "BINANCE_REST_URL": "https://binance.invalid/api/v3",
    "YAHOO_CHART_URL": "https://yahoo.invalid/chart",
    "ALPACA_DATA_URL": "https://alpaca-data.invalid/v2",
    "ALPACA_TRADING_URL": "https://alpaca-paper.invalid",
}


def test_sandbox_requires_fake_market_data() -> None:
    with pytest.raises(ValidationError, match="MARKET_DATA_MODE=fake"):
        Settings(
            DEPLOYMENT_PROFILE="sandbox",
            MARKET_DATA_MODE="live",
            **SANDBOX_ENDPOINTS,
            _env_file=None,
        )


def test_sandbox_rejects_external_market_endpoints() -> None:
    with pytest.raises(ValidationError, match="reserved .invalid hosts"):
        Settings(
            DEPLOYMENT_PROFILE="sandbox",
            MARKET_DATA_MODE="fake",
            **{**SANDBOX_ENDPOINTS, "BINANCE_REST_URL": "https://api.binance.com/api/v3"},
            _env_file=None,
        )


def test_allowed_origins_are_explicit() -> None:
    configured = Settings(
        ALLOWED_ORIGINS="https://market.lab.example,https://admin.lab.example/",
        _env_file=None,
    )

    assert configured.allowed_origins == [
        "https://market.lab.example",
        "https://admin.lab.example",
    ]
