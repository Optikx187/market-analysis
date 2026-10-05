import pytest
from pydantic import ValidationError

from app.config import Settings


def test_sandbox_hard_disables_live_trading() -> None:
    with pytest.raises(ValidationError, match="cannot enable live trading"):
        Settings(
            DEPLOYMENT_PROFILE="sandbox",
            LIVE_TRADING_ENABLED=True,
            LIVE_BROKER_BASE_URL="https://broker.invalid",
            _env_file=None,
        )


def test_sandbox_rejects_external_broker_endpoints() -> None:
    with pytest.raises(ValidationError, match="broker endpoint"):
        Settings(
            DEPLOYMENT_PROFILE="sandbox",
            LIVE_TRADING_ENABLED=False,
            LIVE_BROKER_BASE_URL="https://paper-api.alpaca.markets",
            _env_file=None,
        )


def test_sandbox_accepts_reserved_broker_boundary() -> None:
    configured = Settings(
        DEPLOYMENT_PROFILE="sandbox",
        LIVE_TRADING_ENABLED=False,
        LIVE_BROKER_BASE_URL="https://broker.invalid",
        _env_file=None,
    )

    assert configured.LIVE_TRADING_ENABLED is False
