from functools import lru_cache
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Pharmaceutical Analog Uptake Workbench"
    environment: str = "local"  # local | aws
    database_url: str = "sqlite+aiosqlite:///./storage/workbench.db"
    db_host: str | None = None
    db_name: str | None = None
    db_user: str | None = None
    db_password: str | None = None
    aws_region: str = "us-east-1"
    aws_profile: str = "default"
    s3_bucket: str | None = None
    sqs_queue_url: str | None = None
    storage_backend: str = "local"  # local | s3
    job_backend: str = "inprocess"  # inprocess | sqs
    local_storage_root: str = "./storage"
    openrouter_api_key: str | None = None
    openrouter_model_extract: str = "openai/gpt-4o-mini"
    openrouter_model_judge: str = "openai/gpt-4o-mini"
    # The region fingerprinter: the model that describes where and how a
    # document states product revenue. A fast model describes first; a part
    # whose description the parser rejects or that leaves a product grid
    # undescribed is described again by the strong model. Repairs always use
    # the strong model. Empty fast model = strong model only.
    openrouter_model_fingerprint: str = "anthropic/claude-sonnet-4.5"
    openrouter_model_fingerprint_fast: str = "anthropic/claude-haiku-4.5"
    # model: the description is the only interpreter (scored); degraded: the
    # header grammar and regex prose readers, for runs with no model, never
    # scored; auto: model when an API key is present, else degraded.
    fingerprint_mode: str = "auto"
    enable_llm_fingerprint: bool = True
    fingerprint_concurrency: int = 4
    fingerprint_max_tokens: int = 16000
    fingerprint_max_calls_per_job: int = 400
    # The legacy two-pass LLM extractor and table fingerprint reader; off in
    # model mode, kept until the cleanup phase.
    legacy_revenue_extractors: bool = False
    sec_history_years: int = 12
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    validation_sample_rate: float = 0.10
    max_concurrent_jobs: int = 1
    sec_max_filings: int = 4
    sec_include_8k: bool = False
    # Quarterly product revenue lives in 8-K item 2.02 exhibit 99.x earnings releases,
    # not in the 8-K primary document.
    sec_earnings_exhibits: bool = True
    sec_max_earnings_exhibits: int = 6
    sec_user_agent: str = "PharmaAnalogUptakeWorkbench research@example.com"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    llm_skip_judge_when_deterministic: bool = True
    llm_max_extract_sources: int = 6
    # Independent-search judging of product profile fields. Source registries carry
    # errors (openFDA lists Tyvaso, an inhaled product, as ORAL), so cited fields are
    # challenged rather than passed through.
    enable_profile_judge: bool = True
    # 0 = judge every content field (no cap). Positive values keep an optional budget.
    profile_judge_max_fields: int = 0
    profile_judge_min_confidence: float = 0.6
    enable_llm_search: bool = True
    llm_search_max_queries: int = 4
    llm_search_max_urls: int = 5
    # OpenRouter openrouter:web_search engine: auto | native | exa | parallel | perplexity
    llm_search_engine: str = "auto"
    # Empty = no domain filter (prompt steers to SEC/IR). Comma-separated if set.
    llm_search_allowed_domains: str = ""

    @property
    def resolved_database_url(self) -> str:
        if self.db_host:
            user = quote_plus(self.db_user or "workbench")
            password = quote_plus(self.db_password or "")
            name = self.db_name or "workbench"
            return f"postgresql+psycopg2://{user}:{password}@{self.db_host}:5432/{name}"
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
