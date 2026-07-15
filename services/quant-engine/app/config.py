from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    RISK_REWARD_RATIO: float = 3.0
    ATR_STOP_MULTIPLIER: float = 1.5
    TRAILING_STOP_PCT: float = 0.02
    ATR_VOLATILITY_THRESHOLD: float = 2.0
    DATA_INGESTION_URL: str = "http://data-ingestion:8000"
    PORTFOLIO_ENGINE_URL: str = "http://portfolio-engine:8002"
    NOTIFICATION_GATEWAY_URL: str = "http://notification-gateway:8003"
    SCAN_INTERVAL_MINUTES: int = 15
    SCAN_ENABLED: bool = True
    MARKET_HOURS_ONLY: bool = True

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
