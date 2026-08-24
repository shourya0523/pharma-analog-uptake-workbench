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
    max_concurrent_jobs: int = 3
    sec_user_agent: str = "PharmaAnalogUptakeWorkbench research@example.com"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
