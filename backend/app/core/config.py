from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPIDERMIND_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./spidermind.db"
    user_agent: str = "SpiderMindBot/0.1 (+local research crawler)"
    request_timeout_seconds: float = 10.0
    crawl_timeout_seconds: float = 120.0
    domain_delay_seconds: float = 1.0
    max_redirects: int = 5
    max_retries: int = 2
    embedding_model_name: str = "BAAI/bge-m3"
    embedding_batch_size: int = Field(default=16, ge=1, le=256)
    max_candidate_context_chars: int = Field(default=280, ge=0, le=2000)
    max_page_scoring_chars: int = Field(default=1200, ge=0, le=8000)
    depth_penalty: float = Field(default=0.02, ge=0, le=0.1)
    exploration_rate: float = Field(default=0.10, ge=0, le=1)
    default_min_relevance_score: float | None = Field(default=None, ge=-1, le=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
