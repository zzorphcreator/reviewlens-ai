from functools import lru_cache
from pathlib import Path
import os
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ReviewLens AI"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/reviewlens"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str | None = None
    queue_mode: Literal["inline", "rq"] = "inline"
    upload_dir: Path = Path("uploads")
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    scraper_provider_order: str = "http,brightdata,zyte"
    scraper_timeout_seconds: float = Field(default=30.0, ge=1)
    brightdata_api_key: str | None = None
    brightdata_zone: str | None = None
    brightdata_api_url: str = "https://api.brightdata.com/request"
    brightdata_proxy_url: str | None = None
    brightdata_verify_ssl: bool = True
    brightdata_timeout_seconds: float = Field(default=120.0, ge=1)
    brightdata_debug_dump_html: bool = False
    zyte_api_key: str | None = None
    zyte_api_url: str = "https://api.zyte.com/v1/extract"
    zyte_browser_html: bool = True
    zyte_timeout_seconds: float = Field(default=60.0, ge=1)
    scraper_debug_dump_html: bool = False
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_chat_model: str = "gpt-5.4"
    anthropic_fallback_models: str = "claude-haiku-4.7,claude-sonnet-4.7"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    rag_top_k: int = Field(default=8, ge=1, le=100)
    llm_timeout_seconds: float = Field(default=60.0, ge=1)

    @model_validator(mode="after")
    def _reject_local_database_in_docker(self) -> "Settings":
        if os.getenv("RUNNING_IN_DOCKER") == "1":
            lowered = self.database_url.lower()
            if "@localhost" in lowered or "@127.0.0.1" in lowered:
                raise ValueError(
                    "DATABASE_URL points to localhost inside Docker. Use the db service hostname."
                )
        return self

    @property
    def scraper_providers(self) -> list[str]:
        return [
            provider.strip().lower()
            for provider in self.scraper_provider_order.split(",")
            if provider.strip()
        ]

    @property
    def anthropic_models(self) -> list[str]:
        return [model.strip() for model in self.anthropic_fallback_models.split(",") if model.strip()]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
