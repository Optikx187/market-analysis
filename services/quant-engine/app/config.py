from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    RISK_REWARD_RATIO: float = 3.0
    ATR_STOP_MULTIPLIER: float = 1.5
    TRAILING_STOP_PCT: float = 0.02
    ATR_VOLATILITY_THRESHOLD: float = 2.0
    DATA_INGESTION_URL: str = "http://data-ingestion:8000"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
