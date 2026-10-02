from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./data_ingestion.db"
    QUANT_ENGINE_URL: str = "http://quant-engine:8001"
    PORTFOLIO_ENGINE_URL: str = "http://portfolio-engine:8002"
    INTERNAL_SERVICE_TOKEN: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
