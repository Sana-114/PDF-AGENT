from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "PaperPilot"
    app_version: str = "0.2.0"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    frontend_origin: str = "http://localhost:3000"

    database_url: str = "sqlite:///./data/pdfagent.db"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    vector_search_enabled: bool = False
    vector_collection: str = "paperpilot_chunks_v1"
    vector_prefetch_k: int = 24
    vector_timeout_seconds: float = 3.0

    data_dir: Path = Path("./data")
    upload_dir: Path = Path("./data/uploads")
    parsed_dir: Path = Path("./data/parsed")
    max_upload_mb: int = 200
    celery_task_always_eager: bool = True

    ocr_enabled: bool = True
    ocr_languages: str = "eng+chi_sim"
    ocr_dpi: int = 300
    ocr_min_text_chars: int = 32
    ocr_tessdata: str = ""

    llm_provider: str = "mock"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = 60.0
    rag_min_score: float = 0.18
    embedding_provider: str = "hash"
    embedding_model: str = "hash-ngram-v1"
    embedding_dimensions: int = 384
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.upload_dir, self.parsed_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
