from app.core.config import Settings, settings
from app.llm.base import LLMConfigurationError, LLMProvider
from app.llm.deepseek_responses import DeepSeekResponsesProvider
from app.llm.extractive import ExtractiveProvider
from app.llm.openai_responses import OpenAIResponsesProvider


def get_llm_provider(config: Settings = settings) -> LLMProvider:
    provider = config.llm_provider.casefold().strip()
    if provider in {"mock", "extractive", "none"}:
        return ExtractiveProvider()
    if provider == "openai":
        return OpenAIResponsesProvider(
            api_key=config.llm_api_key,
            model=config.llm_model,
            base_url=config.llm_base_url,
            timeout_seconds=config.llm_timeout_seconds,
        )
    if provider == "deepseek":
        return DeepSeekResponsesProvider(
            api_key=config.llm_api_key,
            model=config.llm_model,
            base_url=config.llm_base_url,
            timeout_seconds=config.llm_timeout_seconds,
        )
    raise LLMConfigurationError(f"不支持的 LLM_PROVIDER: {config.llm_provider}")
