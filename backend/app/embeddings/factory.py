from app.core.config import Settings, settings
from app.embeddings.base import EmbeddingProvider
from app.embeddings.hashing import HashEmbeddingProvider
from app.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider


class EmbeddingConfigurationError(RuntimeError):
    pass


def get_embedding_provider(config: Settings = settings) -> EmbeddingProvider:
    provider = config.embedding_provider.casefold().strip()
    if provider == "hash":
        return HashEmbeddingProvider(dimensions=config.embedding_dimensions)
    if provider in {"openai-compatible", "http"}:
        return OpenAICompatibleEmbeddingProvider(
            model=config.embedding_model,
            dimensions=config.embedding_dimensions,
            base_url=config.embedding_base_url,
            api_key=config.embedding_api_key,
            timeout_seconds=config.embedding_timeout_seconds,
        )
    raise EmbeddingConfigurationError(f"不支持的 EMBEDDING_PROVIDER: {config.embedding_provider}")
