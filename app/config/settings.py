"""Configuración centralizada. Todas las variables provienen del entorno o de .env."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    llm_mode: Literal["openai", "deterministic"] = "openai"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_timeout_seconds: float = 60.0

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Vector store
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_collection: str = "localization_qa"
    vector_store_path: str = "./data/chroma"
    knowledge_dir: str = "./knowledge"

    # RAG
    rag_top_k: int = Field(default=12, ge=1, le=50)
    rag_min_score: float = Field(default=0.15, ge=0.0, le=1.0)
    rag_chunk_max_chars: int = Field(default=1200, ge=200)

    # Ejecución
    max_files_per_request: int = Field(default=10, ge=1)
    max_file_size_bytes: int = Field(default=1_048_576, ge=1)
    graph_max_steps: int = Field(default=12, ge=1)
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # Observabilidad
    tracing_enabled: bool = True
    phoenix_collector_endpoint: str = "http://localhost:6006/v1/traces"
    phoenix_project_name: str = "localization-qa"

    @property
    def usa_openai(self) -> bool:
        return self.llm_mode == "openai"


@lru_cache
def get_settings() -> Settings:
    return Settings()
