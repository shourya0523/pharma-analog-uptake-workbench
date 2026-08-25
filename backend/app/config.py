from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Pharmaceutical Analog Uptake Workbench"
    environment: str = "local"  # local | aws
    database_url: str = "sqlite+aiosqlite:///./storage/workbench.db"
    aws_region: str = "us-east-1"
    aws_profile: str = "Sandbox"
    s3_bucket: str | None = None
    sqs_queue_url: str | None = None
    storage_backend: str = "local"  # local | s3
    job_backend: str = "inprocess"  # inprocess | sqs
    local_storage_root: str = "./storage"
    openrouter_api_key: str | None = None
    openrouter_model_extract: str = "openai/gpt-4o-mini"
    openrouter_model_judge: str = "openai/gpt-4o-mini"
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
    enable_llm_search: bool = True
    llm_search_max_queries: int = 4
    llm_search_max_urls: int = 5
    # OpenRouter openrouter:web_search engine: auto | native | exa | parallel | perplexity
    llm_search_engine: str = "auto"
    # Empty = no domain filter (prompt steers to SEC/IR). Comma-separated if set.
    llm_search_allowed_domains: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
