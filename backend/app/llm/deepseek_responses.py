import httpx

from app.llm.openai_responses import OpenAIResponsesProvider


class DeepSeekResponsesProvider(OpenAIResponsesProvider):
    """DeepSeek's OpenAI-compatible Responses API with provider-specific options."""

    name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            provider_name=self.name,
            strict_structured_output=False,
            include_store=False,
            reasoning_effort="none",
            transport=transport,
        )
