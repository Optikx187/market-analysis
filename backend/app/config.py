from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./market_analysis.db"
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    DISCORD_WEBHOOK_URL: Optional[str] = None
    BINANCE_API_KEY: Optional[str] = None
    BINANCE_API_SECRET: Optional[str] = None
    ALPACA_API_KEY: Optional[str] = None
    ALPACA_API_SECRET: Optional[str] = None
    INITIAL_BALANCE: float = 100_000.0
    RISK_REWARD_RATIO: float = 3.0
    ATR_STOP_MULTIPLIER: float = 1.5
    TRAILING_STOP_PCT: float = 0.02
    ATR_VOLATILITY_THRESHOLD: float = 2.0

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
