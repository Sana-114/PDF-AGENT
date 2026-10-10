from time import perf_counter

from sqlalchemy.orm import Session

from app.agent.registry import SkillContext, SkillRegistry, skill_registry
from app.agent.skills import register_builtin_skills
from app.core.config import settings
from app.llm import get_llm_provider
from app.llm.base import LLMConfigurationError, LLMProvider, LLMResponseError
from app.llm.extractive import ExtractiveProvider
from app.schemas.agent import (
    AgentTraceStep,
    AskRequest,
    AskResponse,
)
from app.services.grounded_text import checked_claims, render_checked_claims


class ResearchAgent:
    """Small auditable harness: retrieve, gate, generate, then validate citations."""

    def __init__(
        self,
        session: Session,
        *,
        registry: SkillRegistry = skill_registry,
        provider: LLMProvider | None = None,
    ) -> None:
        register_builtin_skills()
        self.session = session
        self.registry = registry
        self.provider = provider or get_llm_provider()

    async def ask(self, request: AskRequest, *, diversify_documents: bool = False) -> AskResponse:
        trace: list[AgentTraceStep] = []
        started = perf_counter()
        if diversify_documents and request.document_ids:
            pools = []
            for document_id in request.document_ids:
                candidates = await self.registry.execute(
                    "search_evidence",
                    {
                        "question": request.retrieval_query or request.question,
                        "document_ids": [document_id],
                        "top_k": 20,
                    },
                    SkillContext(db=self.session),
                )
                pools.append(
                    [
                        item
                        for item in candidates
                        if item.document_id == document_id and item.source_type != "reference"
                    ]
                )
            evidence = []
            for rank in range(20):
                for pool in pools:
                    if rank < len(pool) and len(evidence) < request.top_k:
                        item = pool[rank].model_copy(
                            update={"evidence_id": f"E{len(evidence) + 1}"}
                        )
                        evidence.append(item)
        else:
            evidence = await self.registry.execute(
                "search_evidence",
                {
                    "question": request.retrieval_query or request.question,
                    "document_ids": request.document_ids,
                    "top_k": request.top_k,
                },
                SkillContext(db=self.session),
            )
        trace.append(
            AgentTraceStep(
                skill="search_evidence",
                status="ok",
                summary=(
                    f"检索到 {len(evidence)} 条候选证据；"
                    f"模式：{evidence[0].retrieval_mode if evidence else 'none'}。"
                ),
                duration_ms=_elapsed_ms(started),
            )
        )

        strong_evidence = [item for item in evidence if item.score >= settings.rag_min_score]
        if not strong_evidence:
            return AskResponse(
                answer="当前文献库中没有足够证据回答该问题。请缩小问题范围或补充相关论文。",
                claims=[],
                evidence=evidence,
                insufficient_evidence=True,
                provider=self.provider.name,
                model=self.provider.model,
                trace=trace,
            )

        generation_started = perf_counter()
        generation_status = "ok"
        generation_summary = f"使用 {self.provider.name} 生成受证据约束的回答。"
        used_provider = self.provider
        try:
            generated = await self.provider.generate_grounded_answer(
                request.question, strong_evidence
            )
        except (LLMConfigurationError, LLMResponseError) as exc:
            fallback = ExtractiveProvider()
            used_provider = fallback
            generated = await fallback.generate_grounded_answer(request.question, strong_evidence)
            generation_status = "fallback"
            generation_summary = f"模型调用不可用，已切换抽取式回答：{exc}"

        claims, rejected = checked_claims(generated.claims, strong_evidence)
        if rejected:
            generation_summary += f" 已移除 {rejected} 条无有效引用或含无依据数值的声明。"
        trace.append(
            AgentTraceStep(
                skill="llm.generate_grounded_answer",
                status=generation_status,
                summary=generation_summary,
                duration_ms=_elapsed_ms(generation_started),
            )
        )
        return AskResponse(
            answer=render_checked_claims(claims),
            claims=claims,
            evidence=strong_evidence,
            insufficient_evidence=not claims,
            provider=used_provider.name,
            model=used_provider.model,
            trace=trace,
        )


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
