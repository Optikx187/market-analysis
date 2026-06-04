from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./portfolio.db"
    INITIAL_BALANCE: float = 10_000.0
    QUANT_ENGINE_URL: str = "http://quant-engine:8001"
    DATA_INGESTION_URL: str = "http://data-ingestion:8000"
    ROBINHOOD_USERNAME: Optional[str] = None
    ROBINHOOD_PASSWORD: Optional[str] = None
    ROBINHOOD_TOTP: Optional[str] = None
    ROBINHOOD_CAPITAL_LIMIT_PCT: float = 0.05

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
