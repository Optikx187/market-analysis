from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./data_ingestion.db"
    BINANCE_API_KEY: Optional[str] = None
    BINANCE_API_SECRET: Optional[str] = None
    ALPACA_API_KEY: Optional[str] = None
    ALPACA_API_SECRET: Optional[str] = None
    QUANT_ENGINE_URL: str = "http://quant-engine:8001"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
