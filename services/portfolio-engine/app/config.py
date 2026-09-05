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
    MAX_PORTFOLIO_HEAT_PCT: float = 0.08
    MAX_TICKER_EXPOSURE_PCT: float = 0.20
    MAX_SECTOR_EXPOSURE_PCT: float = 0.35
    MAX_ASSET_CLASS_EXPOSURE_PCT: float = 0.70
    MAX_DIRECTIONAL_EXPOSURE_PCT: float = 0.80
    MAX_CORRELATED_EXPOSURE_PCT: float = 0.40
    CORRELATION_THRESHOLD: float = 0.75
    DAILY_LOSS_LIMIT_PCT: float = 0.03
    WEEKLY_LOSS_LIMIT_PCT: float = 0.06
    MAX_DRAWDOWN_PCT: float = 0.12
    VOLATILITY_TARGET_PCT: float = 0.15
    FRACTIONAL_KELLY_CAP: float = 0.10
    EQUITY_SHOCK_PCT: float = 0.05
    CRYPTO_SHOCK_PCT: float = 0.20
    ATTRIBUTION_MIN_SAMPLE_SIZE: int = 20
    ATTRIBUTION_CANDLE_INTERVAL: str = "1d"
    PAPER_SPREAD_PCT: float = 0.0005
    PAPER_SLIPPAGE_PCT: float = 0.0005
    PAPER_VOLUME_PARTICIPATION_PCT: float = 0.01
    PAPER_FEE_PCT: float = 0.0
    PAPER_ORDER_CANDLE_INTERVAL: str = "1d"
    ACTION_ITEM_CANDLE_INTERVAL: str = "1d"
    ACTION_STOP_PROXIMITY_PCT: float = 0.25
    ACTION_EARNINGS_WINDOW_DAYS: int = 7
    LIVE_TRADING_ENABLED: bool = False
    LIVE_BROKER: str = "alpaca"
    LIVE_BROKER_BASE_URL: str = "https://paper-api.alpaca.markets"
    LIVE_ACK_PHRASE: str = "ENABLE LIVE TRADING"
    LIVE_MAX_ORDER_NOTIONAL_USD: float = 1_000.0
    LIVE_MAX_PRICE_AGE_SECONDS: float = 300.0
    ALPACA_API_KEY: str = ""
    ALPACA_SECRET_KEY: str = ""
    AUTH_ENABLED: bool = False
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 24

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
