from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "TeacherAgent"
    environment: Literal["development", "production", "test"] = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:5300", "http://127.0.0.1:5300"]

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "teacher"
    postgres_password: str = "teacher_dev_pw"
    postgres_db: str = "teacher_agent"

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    jwt_secret_key: str = "dev-secret-change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    llm_provider: Literal["openai", "anthropic", "ollama"] = "anthropic"
    llm_model: str = "claude-sonnet-5"
    embedding_provider: Literal["openai", "ollama"] = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    # PDF conversion: "text" needs no key but mangles mathematics.
    pdf_converter: Literal["mistral", "gemini", "text"] = "text"
    pdf_convert_timeout: int = 600
    mistral_api_key: str | None = None
    mistral_ocr_model: str = "mistral-ocr-4"
    gemini_api_key: str | None = None
    gemini_vision_model: str = "gemini-2.5-flash-lite"

    # USD per 1M tokens, (input, output). Verified 2026-07.
    # MODEL_PRICES in the environment merges into this rather than replacing it,
    # so adding one model does not silently drop the rest.
    extra_model_prices: dict[str, tuple[float, float]] = Field(
        default_factory=dict, alias="MODEL_PRICES"
    )
    default_model_prices: dict[str, tuple[float, float]] = {
        "claude-sonnet-5": (3.0, 15.0),
        "claude-opus-5": (15.0, 75.0),
        "claude-haiku-4-5-20251001": (1.0, 5.0),
        "gpt-5": (1.25, 10.0),
        "text-embedding-3-small": (0.02, 0.0),
        "text-embedding-3-large": (0.13, 0.0),
    }

    extra_rerank_prices: dict[str, float] = Field(default_factory=dict, alias="RERANK_PRICES")
    # USD per rerank call: these bill per search, not per token.
    default_rerank_prices: dict[str, float] = {
        "rerank-v3.5": 0.002,
        "jina-reranker-v3": 0.0,
        "rerank-2.5-lite": 0.0,
    }

    # Figures from retrieved sections are sent to the model when it can see.
    answer_with_images: bool = True
    max_answer_images: int = 4

    # Retrieval. "llm" reranking reuses the chat model; "jina" is faster and
    # sharper but needs its own key.
    reranker: Literal["jina", "cohere", "voyage", "llm", "none"] = "llm"
    rerank_timeout: int = 60
    retrieval_candidates: int = 30
    retrieval_top_k: int = 6
    jina_api_key: str | None = None
    jina_rerank_model: str = "jina-reranker-v3"
    cohere_api_key: str | None = None
    cohere_rerank_model: str = "rerank-v3.5"
    voyage_api_key: str | None = None
    voyage_rerank_model: str = "rerank-2.5-lite"

    # Off by default: web search only ever runs on an explicit user action.
    web_search_enabled: bool = False
    search_provider: Literal["tavily", "brave", "searxng"] = "tavily"
    tavily_api_key: str | None = None
    web_search_timeout: int = 30
    # Candidates returned to the user, and how many of those the one-shot
    # answer (form A) actually fetches full text for. Both are hard caps.
    web_search_top_k: int = 8
    web_search_fetch_pages: int = 3
    # Per-user calls to /web-search and one-shot web answers, per rolling hour.
    web_search_rate_limit_per_hour: int = 30

    # A few hundred pages of OCR plus embedding can run for many minutes.
    ingest_job_timeout: int = 3600

    max_upload_size_mb: int = 100
    max_repo_size_mb: int = 200
    max_crawl_pages: int = 100
    max_crawl_depth: int = 3
    # SSRF guard. Proxies with fake-IP DNS resolve every host into reserved
    # space, which makes this check fire on any site; such setups turn it off.
    crawl_block_private_addresses: bool = True

    storage_dir: str = "./storage"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def model_prices(self) -> dict[str, tuple[float, float]]:
        return {**self.default_model_prices, **self.extra_model_prices}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rerank_prices(self) -> dict[str, float]:
        return {**self.default_rerank_prices, **self.extra_rerank_prices}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Used by Alembic and the LangGraph Postgres checkpointer, which are both sync."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
