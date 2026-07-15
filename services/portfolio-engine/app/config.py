from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./portfolio.db"
    INITIAL_BALANCE: float = 10_000.0
    QUANT_ENGINE_URL: str = "http://quant-engine:8001"
    DATA_INGESTION_URL: str = "http://data-ingestion:8000"
    NOTIFICATION_GATEWAY_URL: str = "http://notification-gateway:8003"
    RISK_REWARD_RATIO: float = 3.0
    ATR_STOP_MULTIPLIER: float = 1.5
    ATR_VOLATILITY_THRESHOLD: float = 2.0
    TRAILING_STOP_PCT: float = 0.02
    AUTH_ENABLED: bool = False
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 24

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
