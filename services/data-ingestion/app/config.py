from urllib.parse import urlparse

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./data_ingestion.db"
    QUANT_ENGINE_URL: str = "http://quant-engine:8001"
    PORTFOLIO_ENGINE_URL: str = "http://portfolio-engine:8002"
    INTERNAL_SERVICE_TOKEN: str = ""
    DEPLOYMENT_PROFILE: str = "single-node"
    PUBLIC_BASE_URL: str = "http://localhost:3000"
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    MARKET_DATA_MODE: str = "live"
    BINANCE_REST_URL: str = "https://api.binance.com/api/v3"
    YAHOO_CHART_URL: str = "https://query2.finance.yahoo.com/v8/finance/chart"
    ALPACA_DATA_URL: str = "https://data.alpaca.markets/v2"
    ALPACA_TRADING_URL: str = "https://paper-api.alpaca.markets"

    @property
    def allowed_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]

    @model_validator(mode="after")
    def validate_deployment_profile(self) -> "Settings":
        profile = self.DEPLOYMENT_PROFILE.strip().lower()
        mode = self.MARKET_DATA_MODE.strip().lower()
        if mode not in {"live", "fake"}:
            raise ValueError("MARKET_DATA_MODE must be 'live' or 'fake'")
        if profile == "sandbox":
            if mode != "fake":
                raise ValueError("sandbox requires MARKET_DATA_MODE=fake")
            external_hosts = {
                urlparse(url).hostname
                for url in (
                    self.BINANCE_REST_URL,
                    self.YAHOO_CHART_URL,
                    self.ALPACA_DATA_URL,
                    self.ALPACA_TRADING_URL,
                )
            }
            if any(not host or not host.endswith(".invalid") for host in external_hosts):
                raise ValueError("sandbox provider endpoints must use reserved .invalid hosts")
        return self

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
