from pathlib import Path
from uuid import UUID

from pydantic import model_validator
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
    BACKTEST_DATABASE_PATH: str = "./backtests.db"
    RESEARCH_STORAGE_BACKEND: str = "sqlite-transition"
    RESEARCH_DATABASE_URL: str = ""
    RESEARCH_TENANT_ID: str = "00000000-0000-7000-8000-000000000001"
    RESEARCH_USER_KEY: str = "default"
    BACKTEST_ARTIFACT_DIRECTORY: str = "./artifacts"
    BACKTEST_INLINE_RESULT_MAX_BYTES: int = 262_144
    DEPLOYMENT_PROFILE: str = "single-node"
    PUBLIC_BASE_URL: str = "http://localhost:3000"
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def allowed_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]

    @property
    def research_database_url(self) -> str:
        configured = self.RESEARCH_DATABASE_URL.strip()
        if configured:
            if configured.startswith("postgresql://"):
                return configured.replace(
                    "postgresql://",
                    "postgresql+psycopg://",
                    1,
                )
            return configured
        database_path = Path(self.BACKTEST_DATABASE_PATH).resolve()
        return f"sqlite:///{database_path}"

    @model_validator(mode="after")
    def validate_research_storage(self) -> "Settings":
        backend = self.RESEARCH_STORAGE_BACKEND.strip().lower()
        if backend not in {"postgresql", "sqlite-transition"}:
            raise ValueError(
                "RESEARCH_STORAGE_BACKEND must be 'postgresql' "
                "or 'sqlite-transition'"
            )
        database_url = self.research_database_url
        if backend == "postgresql":
            if not database_url.startswith(
                ("postgresql+psycopg://", "postgresql://")
            ):
                raise ValueError(
                    "postgresql research storage requires a PostgreSQL "
                    "RESEARCH_DATABASE_URL"
                )
            UUID(self.RESEARCH_TENANT_ID)
        elif not database_url.startswith("sqlite:///"):
            raise ValueError(
                "sqlite-transition research storage requires a SQLite "
                "RESEARCH_DATABASE_URL"
            )
        if not self.RESEARCH_USER_KEY.strip():
            raise ValueError("RESEARCH_USER_KEY must not be empty")
        if self.BACKTEST_INLINE_RESULT_MAX_BYTES < 1024:
            raise ValueError(
                "BACKTEST_INLINE_RESULT_MAX_BYTES must be at least 1024"
            )
        return self

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
