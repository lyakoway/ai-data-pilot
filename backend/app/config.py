"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Data Pilot"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
    ]

    data_dir: Path = DATA_DIR
    docs_dir: Path = DATA_DIR / "docs"
    exports_dir: Path = DATA_DIR / "exports"
    database_url: str = f"sqlite:///{DATA_DIR / 'ridego.db'}"
    app_db_url: str = f"sqlite:///{DATA_DIR / 'app.db'}"

    sql_row_limit: int = 500
    sql_timeout_sec: float = 8.0
    # External PostgreSQL needs a longer budget: cross-network connect + heavy OLAP queries.
    sql_timeout_pg_sec: float = 30.0
    # small ≈ быстрый cold start (HF Spaces); full ≈ плотнее данные локально
    demo_scale: str = "small"

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    zai_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    ollama_num_gpu: int | None = None

    # Optional pre-configured PostgreSQL analytics source.
    postgres_url: str | None = None

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
