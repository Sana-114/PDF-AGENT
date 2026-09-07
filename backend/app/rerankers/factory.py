from app.core.config import Settings, settings
from app.rerankers.base import Reranker
from app.rerankers.disabled import DisabledReranker
from app.rerankers.http import HttpReranker, RerankerServiceError


def get_reranker(config: Settings = settings) -> Reranker:
    provider = config.reranker_provider.casefold().strip()
    if provider in {"", "none", "disabled"}:
        return DisabledReranker()
    if provider in {"cohere-compatible", "http"}:
        return HttpReranker(
            model=config.reranker_model,
            base_url=config.reranker_base_url,
            api_key=config.reranker_api_key,
            timeout_seconds=config.reranker_timeout_seconds,
            candidate_k=config.reranker_candidate_k,
            retrieval_weight=config.reranker_retrieval_weight,
        )
    raise RerankerServiceError(f"不支持的 RERANKER_PROVIDER: {config.reranker_provider}")
