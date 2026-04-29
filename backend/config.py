from functools import lru_cache
from pathlib import Path
import os
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ReviewLens AI"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/reviewlens"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str | None = None
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
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "reviewlens-ai"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_workspace_id: str | None = None
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_endpoint: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    @model_validator(mode="after")
    def _validate_runtime_settings(self) -> "Settings":
        if os.getenv("RUNNING_IN_DOCKER") == "1":
            lowered = self.database_url.lower()
            if "@localhost" in lowered or "@127.0.0.1" in lowered:
                raise ValueError(
                    "DATABASE_URL points to localhost inside Docker. Use the db service hostname."
                )
        self._configure_langsmith_env()
        return self

    def _configure_langsmith_env(self) -> None:
        if not self.langsmith_tracing:
            os.environ.setdefault("LANGSMITH_TRACING", "false")
            return

        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_PROJECT"] = self.langsmith_project
        os.environ["LANGSMITH_ENDPOINT"] = self.langsmith_endpoint
        if self.langsmith_api_key:
            os.environ["LANGSMITH_API_KEY"] = self.langsmith_api_key
        if self.langsmith_workspace_id:
            os.environ["LANGSMITH_WORKSPACE_ID"] = self.langsmith_workspace_id

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
