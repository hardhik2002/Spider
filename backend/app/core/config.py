from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
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
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "Qwen3:latest"
    planner_timeout_seconds: float = Field(default=180, gt=0, le=600)
    search_retries: int = Field(default=2, ge=0, le=5)
    max_concurrent_searches: int = Field(default=2, ge=1, le=8)
    max_concurrent_subquestion_crawls: int = Field(default=1, ge=1, le=4)
    max_seeds_per_domain_per_subquestion: int = Field(default=1, ge=1, le=10)
    seed_semantic_weight: float = Field(default=0.85, ge=0, le=1)
    chunk_target_tokens: int = Field(default=500, ge=50, le=2000)
    chunk_max_tokens: int = Field(default=650, ge=80, le=2500)
    chunk_overlap_tokens: int = Field(default=80, ge=0, le=500)
    chunk_min_tokens: int = Field(default=80, ge=1, le=500)
    qdrant_path: str = "data/qdrant"
    qdrant_collection: str = "spidermind_chunks"
    agent_checkpoint_path: str = "data/langgraph-checkpoints.sqlite"
    agent_max_iterations: int = Field(default=5, ge=0, le=20)
    agent_max_queries: int = Field(default=12, ge=0, le=100)
    agent_max_seeds: int = Field(default=10, ge=0, le=100)
    agent_max_pages: int = Field(default=40, ge=0, le=300)
    agent_max_runtime_seconds: int = Field(default=900, ge=1, le=7200)
    agent_min_evidence_chunks: int = Field(default=3, ge=1, le=20)
    agent_min_unique_sources: int = Field(default=2, ge=1, le=20)
    agent_max_assessment_chunks: int = Field(default=6, ge=1, le=12)
    agent_max_assessment_chars_per_chunk: int = Field(default=1200, ge=100, le=4000)
    agent_max_total_assessment_chars: int = Field(default=7000, ge=500, le=20000)
    agent_max_queries_per_gap: int = Field(default=2, ge=1, le=5)
    agent_max_stagnant_iterations: int = Field(default=2, ge=1, le=10)
    agent_rerank_enabled: bool = True
    agent_retrieval_top_k: int = Field(default=8, ge=1, le=50)
    agent_model: str | None = None
    agent_temperature: float = Field(default=0, ge=0, le=1)
    agent_search_results_per_query: int = Field(default=8, ge=1, le=20)
    agent_max_pages_per_seed: int = Field(default=4, ge=1, le=100)
    agent_crawl_max_depth: int = Field(default=2, ge=0, le=5)
    dense_top_k: int = Field(default=50, ge=1, le=200)
    lexical_top_k: int = Field(default=50, ge=1, le=200)
    fusion_top_k: int = Field(default=30, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1, le=200)
    rrf_dense_weight: float = Field(default=1.0, ge=0, le=10)
    rrf_lexical_weight: float = Field(default=1.0, ge=0, le=10)
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_device: str = "cpu"
    reranker_batch_size: int = Field(default=4, ge=1, le=64)
    reranker_candidate_count: int = Field(default=30, ge=1, le=100)
    rerank_enabled: bool = True
    final_top_k: int = Field(default=8, ge=1, le=50)
    max_final_chunks_per_document: int = Field(default=2, ge=1, le=20)
    neighbor_expansion_enabled: bool = True
    neighbor_window: int = Field(default=1, ge=0, le=3)
    max_neighbor_context_tokens: int = Field(default=700, ge=50, le=3000)

    @model_validator(mode="after")
    def valid_chunk_profile(self):
        if not (self.chunk_min_tokens <= self.chunk_target_tokens <= self.chunk_max_tokens):
            raise ValueError("chunk_min_tokens <= chunk_target_tokens <= chunk_max_tokens required")
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("chunk_overlap_tokens must be less than chunk_target_tokens")
        return self

    @field_validator("ollama_url")
    @classmethod
    def local_ollama_only(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme != "http"
            or parts.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parts.username
            or parts.password
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise ValueError("ollama_url must be a local loopback HTTP endpoint")
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
