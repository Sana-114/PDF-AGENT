import re
from collections import Counter
from dataclasses import dataclass

from app.llm.base import LLMProvider, LLMResponseError
from app.schemas.academic_translation import (
    AcademicTranslationRead,
    AcademicTranslationRequest,
    GlossaryApplicationRead,
    PreservationCheckRead,
)

MAX_SEGMENTS_PER_REQUEST = 40
MAX_CHARACTERS_PER_REQUEST = 12000
PARAGRAPH_SEPARATOR_RE = re.compile(r"(\n\s*\n)")
PROTECTED_SPAN_RE = re.compile(
    r"```[\s\S]*?```"
    r"|\\begin\{[^{}]+\}[\s\S]*?\\end\{[^{}]+\}"
    r"|\\\[[\s\S]*?\\\]"
    r"|\\\([\s\S]*?\\\)"
    r"|(?<!\\)\$[^$\n]+(?<!\\)\$"
    r"|`[^`\n]+`"
    r"|\[(?:\d{1,4})(?:\s*[-,–]\s*\d{1,4})*\]"
    r"|https?://[^\s<>]+"
    r"|10\.\d{4,9}/[^\s<>]+"
    r"|(?<![\w])(?:\d+(?:\.\d+)?(?:[eE][+-]?\d+)?%?)(?![\w])"
)


@dataclass(frozen=True)
class ProtectedSpan:
    token: str
    value: str
    kind: str


def _letters(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _span_kind(value: str) -> str:
    if value.startswith("```") or (value.startswith("`") and value.endswith("`")):
        return "code"
    if (
        value.startswith("$")
        or value.startswith(r"\(")
        or value.startswith(r"\[")
        or value.startswith(r"\begin")
    ):
        return "formula"
    if value.startswith("["):
        return "citation"
    if value.startswith("http") or value.startswith("10."):
        return "url"
    return "number"


def _protect_source(
    text: str,
    glossary: list[tuple[str, str]],
) -> tuple[str, list[ProtectedSpan], list[GlossaryApplicationRead]]:
    spans: list[ProtectedSpan] = []

    def protect(value: str, kind: str) -> str:
        candidate_index = len(spans)
        token = f"`__PAPERPILOT_{_letters(candidate_index)}__`"
        while token in text or any(item.token == token for item in spans):
            candidate_index += 1
            token = f"`__PAPERPILOT_{_letters(candidate_index)}__`"
        spans.append(ProtectedSpan(token=token, value=value, kind=kind))
        return token

    protected = PROTECTED_SPAN_RE.sub(
        lambda match: protect(match.group(0), _span_kind(match.group(0))),
        text,
    )
    applications: list[GlossaryApplicationRead] = []
    for source, target in sorted(glossary, key=lambda item: len(item[0]), reverse=True):
        count = protected.count(source)
        if not count:
            continue
        applications.append(
            GlossaryApplicationRead(source=source, target=target, count=count)
        )
        for _ in range(count):
            protected = protected.replace(source, protect(target, "glossary"), 1)
    return protected, spans, applications


def _translation_parts(text: str) -> tuple[list[str], list[tuple[str, str]], dict[str, int]]:
    parts = PARAGRAPH_SEPARATOR_RE.split(text)
    segments: list[tuple[str, str]] = []
    positions: dict[str, int] = {}
    for index, part in enumerate(parts):
        if not part.strip() or PARAGRAPH_SEPARATOR_RE.fullmatch(part):
            continue
        block_id = f"segment-{len(segments) + 1}"
        segments.append((block_id, part))
        positions[block_id] = index
    return parts, segments, positions


def _batch_segments(segments: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    batches: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_characters = 0
    for segment in segments:
        segment_characters = len(segment[1])
        exceeds_budget = (
            current
            and (
                len(current) >= MAX_SEGMENTS_PER_REQUEST
                or current_characters + segment_characters > MAX_CHARACTERS_PER_REQUEST
            )
        )
        if exceeds_budget:
            batches.append(current)
            current = []
            current_characters = 0
        current.append(segment)
        current_characters += segment_characters
    if current:
        batches.append(current)
    return batches


def _restore_spans(translation: str, spans: list[ProtectedSpan]) -> str:
    restored = translation
    for span in spans:
        occurrence_count = restored.count(span.token)
        if occurrence_count != 1:
            raise LLMResponseError(
                "翻译结果未完整保留公式、代码、引用、数字或术语占位符。"
            )
        restored = restored.replace(span.token, span.value)
    return restored


class AcademicTranslationService:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def translate(
        self,
        request: AcademicTranslationRequest,
    ) -> AcademicTranslationRead:
        glossary = [(item.source, item.target) for item in request.glossary]
        protected, spans, glossary_applied = _protect_source(request.text, glossary)
        parts, segments, positions = _translation_parts(protected)
        if not segments:
            raise LLMResponseError("待翻译文本没有可处理的段落。")
        batches = _batch_segments(segments)
        translated_segments: dict[str, str] = {}
        context = (
            "Write a concise journal-style abstract with clear objective, method, result, and "
            "conclusion wording where those elements exist in the source. Do not invent missing "
            "elements."
            if request.document_type == "abstract"
            else "Write coherent formal academic prose suitable for a research paper. Preserve "
            "headings, lists, paragraph roles, and the strength of every claim."
        )
        for batch in batches:
            result = await self.provider.translate_segments(
                batch,
                request.source_language,
                request.target_language,
                context,
            )
            expected_ids = {block_id for block_id, _ in batch}
            invalid_items = any(not value.strip() for value in result.items.values())
            if set(result.items) != expected_ids or invalid_items:
                raise LLMResponseError("翻译结果缺少段落或包含未知段落 ID。")
            translated_segments.update(result.items)
        for block_id, translation in translated_segments.items():
            parts[positions[block_id]] = translation
        restored = _restore_spans("".join(parts), spans).strip()

        warnings: list[str] = []
        if not glossary_applied and request.glossary:
            warnings.append("提供的术语未在原文中出现，因此本次没有应用术语替换。")
        if restored == request.text:
            warnings.append("译文与原文完全相同，请核对源语言、目标语言和模型配置。")
        kind_counts = Counter(span.kind for span in spans if span.kind != "glossary")
        preservation_checks = [
            PreservationCheckRead(kind=kind, count=count)
            for kind, count in kind_counts.items()
        ]
        return AcademicTranslationRead(
            translation=restored,
            source_language=request.source_language,
            target_language=request.target_language,
            document_type=request.document_type,
            provider=self.provider.name,
            model=self.provider.model,
            source_characters=len(request.text),
            translated_characters=len(restored),
            paragraph_count=len(segments),
            request_count=len(batches),
            glossary_applied=glossary_applied,
            preservation_checks=preservation_checks,
            warnings=warnings,
        )
