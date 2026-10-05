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


@lru_cache
def get_settings() -> Settings:
    return Settings()
