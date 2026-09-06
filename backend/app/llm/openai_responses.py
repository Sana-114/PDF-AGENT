import json

import httpx

from app.llm.base import (
    GeneratedAnswer,
    GeneratedClaim,
    LLMConfigurationError,
    LLMResponseError,
)
from app.schemas.agent import EvidenceAnchor


class OpenAIResponsesProvider:
    """OpenAI Responses API adapter with schema-constrained, grounded output."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        if not api_key:
            raise LLMConfigurationError("LLM_PROVIDER=openai 时必须配置 LLM_API_KEY。")
        if not model:
            raise LLMConfigurationError("LLM_PROVIDER=openai 时必须配置 LLM_MODEL。")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def generate_grounded_answer(
        self, question: str, evidence: list[EvidenceAnchor]
    ) -> GeneratedAnswer:
        allowed_ids = {item.evidence_id for item in evidence}
        evidence_text = "\n\n".join(
            (
                f"[{item.evidence_id}] document={item.document_title or item.document_id}; "
                f"page={item.page_number}; section={item.section or '-'}\n{item.quote}"
            )
            for item in evidence
        )
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "answer": {"type": "string"},
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "text": {"type": "string"},
                            "evidence_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["text", "evidence_ids"],
                    },
                },
            },
            "required": ["answer", "claims"],
        }
        payload = {
            "model": self.model,
            "store": False,
            "instructions": (
                "You are an evidence-grounded research assistant. Answer in the language of the "
                "question. Use only the supplied evidence. Every factual claim must cite one or "
                "more supplied evidence IDs. If evidence is insufficient, say so explicitly."
            ),
            "input": f"Question:\n{question}\n\nEvidence:\n{evidence_text}",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "grounded_answer",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/responses", json=payload, headers=headers
                )
        except httpx.HTTPError as exc:
            raise LLMResponseError(f"OpenAI Responses API 网络请求失败：{exc}") from exc
        if response.is_error:
            raise LLMResponseError(
                f"OpenAI Responses API 返回 HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        raw = response.json()
        output_text = self._extract_output_text(raw)
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise LLMResponseError("模型未返回有效的结构化 JSON。") from exc

        claims: list[GeneratedClaim] = []
        for item in parsed.get("claims", []):
            cited = [value for value in item.get("evidence_ids", []) if value in allowed_ids]
            if cited:
                claims.append(GeneratedClaim(text=str(item.get("text", "")), evidence_ids=cited))
        if parsed.get("claims") and not claims:
            raise LLMResponseError("模型答案未引用任何有效证据锚点。")
        return GeneratedAnswer(answer=str(parsed.get("answer", "")), claims=claims)

    @staticmethod
    def _extract_output_text(response: dict) -> str:
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return str(content["text"])
        raise LLMResponseError("Responses API 响应中没有 output_text。")
