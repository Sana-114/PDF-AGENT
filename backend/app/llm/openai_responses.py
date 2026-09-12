import json

import httpx

from app.llm.base import (
    GeneratedAnswer,
    GeneratedClaim,
    GeneratedSegmentTranslations,
    GeneratedTranslation,
    LLMConfigurationError,
    LLMResponseError,
)
from app.schemas.agent import EvidenceAnchor


class OpenAIResponsesProvider:
    """OpenAI Responses API adapter with schema-constrained, grounded output."""

    name = "openai"
    supports_translation = True

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

    async def translate_text(
        self, text: str, source_language: str, target_language: str
    ) -> GeneratedTranslation:
        language_names = {"auto": "automatically detected", "zh": "Chinese", "en": "English"}
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"translation": {"type": "string"}},
            "required": ["translation"],
        }
        payload = {
            "model": self.model,
            "store": False,
            "instructions": (
                "You are an academic translator. Translate faithfully without adding facts, "
                "explanations, or citations. Preserve equations, citation markers, code, model "
                "names, numbers, and paragraph structure. Use precise academic terminology."
            ),
            "input": (
                f"Source language: {language_names[source_language]}\n"
                f"Target language: {language_names[target_language]}\n\n"
                f"Text:\n{text}"
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "academic_translation",
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

        try:
            parsed = json.loads(self._extract_output_text(response.json()))
        except json.JSONDecodeError as exc:
            raise LLMResponseError("翻译模型未返回有效的结构化 JSON。") from exc
        translation = str(parsed.get("translation", "")).strip()
        if not translation:
            raise LLMResponseError("翻译模型返回了空结果。")
        return GeneratedTranslation(text=translation)

    async def translate_segments(
        self,
        segments: list[tuple[str, str]],
        source_language: str,
        target_language: str,
        context: str | None = None,
    ) -> GeneratedSegmentTranslations:
        if not segments:
            return GeneratedSegmentTranslations(items={})
        language_names = {"auto": "automatically detected", "zh": "Chinese", "en": "English"}
        block_ids = [block_id for block_id, _ in segments]
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "translations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "block_id": {"type": "string", "enum": block_ids},
                            "translation": {"type": "string"},
                        },
                        "required": ["block_id", "translation"],
                    },
                }
            },
            "required": ["translations"],
        }
        source_payload = json.dumps(
            [{"block_id": block_id, "text": text} for block_id, text in segments],
            ensure_ascii=False,
        )
        payload = {
            "model": self.model,
            "store": False,
            "instructions": (
                "You are an academic translator. Translate every supplied segment independently "
                "and return each original block_id exactly once. Do not merge, omit, or reorder "
                "segments. Do not add facts or explanations. Preserve equations, citation markers, "
                "code, model names, numbers, and protected placeholder tokens exactly. "
                + (context or "Use precise formal academic prose.")
            ),
            "input": (
                f"Source language: {language_names[source_language]}\n"
                f"Target language: {language_names[target_language]}\n\n"
                f"Segments JSON:\n{source_payload}"
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "aligned_academic_translation",
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
        try:
            parsed = json.loads(self._extract_output_text(response.json()))
        except json.JSONDecodeError as exc:
            raise LLMResponseError("分段翻译模型未返回有效的结构化 JSON。") from exc

        translated: dict[str, str] = {}
        allowed_ids = set(block_ids)
        for item in parsed.get("translations", []):
            block_id = str(item.get("block_id", ""))
            translation = str(item.get("translation", "")).strip()
            if block_id not in allowed_ids or block_id in translated or not translation:
                raise LLMResponseError("分段翻译返回了无效、重复或空的块。")
            translated[block_id] = translation
        if set(translated) != allowed_ids:
            raise LLMResponseError("分段翻译未完整保留全部段落 ID。")
        return GeneratedSegmentTranslations(items=translated)

    @staticmethod
    def _extract_output_text(response: dict) -> str:
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return str(content["text"])
        raise LLMResponseError("Responses API 响应中没有 output_text。")
