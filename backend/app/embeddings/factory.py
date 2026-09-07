from app.core.config import Settings, settings
from app.embeddings.base import EmbeddingProvider
from app.embeddings.hashing import HashEmbeddingProvider


class EmbeddingConfigurationError(RuntimeError):
    pass


def get_embedding_provider(config: Settings = settings) -> EmbeddingProvider:
    provider = config.embedding_provider.casefold().strip()
    if provider == "hash":
        return HashEmbeddingProvider(dimensions=config.embedding_dimensions)
    raise EmbeddingConfigurationError(f"不支持的 EMBEDDING_PROVIDER: {config.embedding_provider}")
