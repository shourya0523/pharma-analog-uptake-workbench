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
    # Bedrock models (Claude Converse for extract/judge; GPT + mantle web search for search)
    bedrock_model_extract: str = "us.anthropic.claude-sonnet-4-6"
    bedrock_model_judge: str = "us.anthropic.claude-sonnet-4-6"
    bedrock_model_search: str = "openai.gpt-5.6-terra"
    bedrock_mantle_base_url: str | None = None
    bedrock_max_tokens: int = 4096
    validation_sample_rate: float = 0.10
    max_concurrent_jobs: int = 1
    sec_max_filings: int = 4
    sec_include_8k: bool = False
    sec_user_agent: str = "PharmaAnalogUptakeWorkbench research@example.com"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    llm_skip_judge_when_deterministic: bool = True
    llm_max_extract_sources: int = 3
    enable_llm_search: bool = True
    llm_search_max_queries: int = 4
    llm_search_max_urls: int = 5
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

    @property
    def mantle_base_url(self) -> str:
        if self.bedrock_mantle_base_url:
            return self.bedrock_mantle_base_url.rstrip("/")
        return f"https://bedrock-mantle.{self.aws_region}.api.aws/openai/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
