from functools import lru_cache

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
    embedding_batch_size: int = 16
    max_candidate_context_chars: int = 280
    max_page_scoring_chars: int = 1200
    depth_penalty: float = 0.02
    exploration_rate: float = 0.10
    default_min_relevance_score: float | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
