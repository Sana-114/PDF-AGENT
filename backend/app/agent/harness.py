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
from app.services.answer_completeness import (
    contract_kind,
    contract_kinds,
    contract_prompt,
    current_question,
    missing_fields,
)
from app.services.baseline_evidence import baseline_citations, matches_baseline_reference
from app.services.grounded_text import (
    audit_grounded_claims,
    checked_claims,
    only_source_limitations,
    render_checked_claims,
)
from app.services.normalization_evidence import missing_normalization_quotes
from app.services.optimizer_evidence import missing_optimizer_beta_claims
from app.services.relation_review import review_relational_claims
from app.services.retrieval_policy import EXPLANATION_QUERY, comparison_query, explanation_statement
from app.services.table_comparison import (
    remove_table_bleu_refusals,
    table_bleu_claims,
    table_bleu_proof_published,
)
from app.services.writing_context import expand_writing_evidence


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

    async def ask(
        self,
        request: AskRequest,
        *,
        diversify_documents: bool = False,
        include_writing_context: bool = False,
    ) -> AskResponse:
        trace: list[AgentTraceStep] = []
        started = perf_counter()
        diversify_documents = diversify_documents or bool(
            request.document_ids
            and len(request.document_ids) > 1
            and (contract_kind(request.question) or comparison_query(request.question))
        )
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
                scoped = [item for item in candidates if item.document_id == document_id]
                # A context baseline's author/year reference establishes source
                # identity. Do not discard that already scored bridge when
                # interleaving papers, or admit unrelated bibliography entries.
                citations = (
                    baseline_citations(scoped)
                    if ("model_context" in contract_kinds(request.question))
                    else []
                )
                identity = next(
                    (
                        item
                        for item in scoped
                        if item.score >= settings.rag_min_score
                        and any(
                            matches_baseline_reference(item, author, year)
                            for _, author, year in citations
                        )
                    ),
                    None,
                )
                pools.append(
                    [item for item in scoped if item.source_type != "reference" or item is identity]
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
        if request.document_ids:
            scope = set(request.document_ids)
            scoped = [item for item in evidence if item.document_id in scope]
            if len(scoped) != len(evidence):
                trace.append(
                    AgentTraceStep(
                        skill="audit_evidence_scope",
                        status="rejected",
                        summary=f"已移除 {len(evidence) - len(scoped)} 条当前所选文献之外的证据。",
                        duration_ms=0,
                    )
                )
            evidence = scoped
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
        if EXPLANATION_QUERY.search(current_question(request.question)):
            # Keep every original ID, score, quote and anchor. Put available causal
            # body prose before abstracts so it is not lost in a long evidence pool.
            strong_evidence = sorted(
                strong_evidence, key=lambda item: not explanation_statement(item)
            )
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

        if include_writing_context:
            original_count = len(strong_evidence)
            strong_evidence = expand_writing_evidence(strong_evidence)
            trace.append(
                AgentTraceStep(
                    skill="expand_writing_context",
                    status="ok" if len(strong_evidence) > original_count else "skipped",
                    summary=(
                        f"追加 {len(strong_evidence) - original_count} 条摘要／原页相邻正文；"
                        "补充文本有独立页码与块锚点，未独立计算检索分数。"
                    ),
                    duration_ms=0,
                )
            )

        generation_started = perf_counter()
        generation_status = "ok"
        generation_summary = f"使用 {self.provider.name} 生成受证据约束的回答。"
        used_provider = self.provider
        try:
            generated = await self.provider.generate_grounded_answer(
                contract_prompt(request.question, strong_evidence), strong_evidence
            )
        except (LLMConfigurationError, LLMResponseError) as exc:
            fallback = ExtractiveProvider()
            used_provider = fallback
            generated = await fallback.generate_grounded_answer(request.question, strong_evidence)
            generation_status = "fallback"
            generation_summary = f"模型调用不可用，已切换抽取式回答：{exc}"

        table_claims = table_bleu_claims(current_question(request.question), strong_evidence)
        claims, grounding_audit = audit_grounded_claims(
            [*generated.claims, *table_claims], strong_evidence,
            question=current_question(request.question),
        )
        causal_started = perf_counter()
        before_causal = len(claims)
        claims, causal_audit = await review_relational_claims(
            used_provider, claims, strong_evidence,
        )
        if causal_audit is not None:
            withheld = before_causal - len(claims)
            trace.append(AgentTraceStep(
                skill="llm.review_relational_claims",
                status="ok" if not withheld else "rejected",
                summary=(f"按各声明已选原文校审 {len(causal_audit['claim_indices'])} 条"
                         "因果或数学关系表述；"
                         f"保留 {len(causal_audit['claim_indices']) - withheld} 条、"
                         f"暂缓 {withheld} 条。"
                         "未改写、补引或自动重答；校审仍可能出错。"),
                duration_ms=_elapsed_ms(causal_started),
            ))
        rejected = len(grounding_audit.rejections)
        if rejected:
            generation_summary += f" 已移除 {rejected} 条引用、数值或可检测语义冲突未通过的声明。"
        trace.append(
            AgentTraceStep(
                skill="llm.generate_grounded_answer",
                status=generation_status,
                summary=generation_summary,
                duration_ms=_elapsed_ms(generation_started),
            )
        )
        if generated.comparison_review_status:
            reviewed = generated.comparison_review_status
            trace.append(AgentTraceStep(
                skill="llm.review_comparison_source",
                status="ok" if reviewed in {"supported", "revised"} else "rejected",
                summary={
                    "supported": ("两份前提及程序组成的机制对照通过一次独立校审；"
                                  "未补引，不替代独立科学验收。"),
                    "revised": "一次单独证据校审收敛了比较措辞，保留原稿与已选来源，未补引。",
                }.get(reviewed, "单独证据校审未通过，比较暂缓；没有自动重答。"),
                duration_ms=0,
            ))
        if generated.field_rejections:
            trace.append(
                AgentTraceStep(
                    skill="audit_structured_field_support",
                    status="rejected",
                    summary="已单独保留失败栏目：" + "；".join(
                        f"{key}：{reason}" for key, reason in generated.field_rejections.items()
                    ) + "。不补写引用或量值，其他栏目继续逐条核验。",
                    duration_ms=0,
                )
            )
        if generated.citation_normalizations:
            trace.append(AgentTraceStep(
                skill="normalize_explicit_citations", status="ok",
                summary="将模型已明确选择的来源引用渲染到对应数组：" + "；".join(
                    f"{key}→{','.join(ids)}"
                    for key, ids in generated.citation_normalizations.items()
                ) + "。没有推测新编号，原始输出保留，仍需来源与数值审计。",
                duration_ms=0,
            ))
        if table_claims:
            trace.append(
                AgentTraceStep(
                    skill="calculate_table_comparison",
                    status="ok",
                    summary=(
                        "同一原文表格、同一 BLEU 任务的模型行与参照行确定性比较；差值标为推导。"
                    ),
                    duration_ms=0,
                )
            )
        claims, table_refusals = remove_table_bleu_refusals(
            current_question(request.question), claims, table_claims
        )
        if table_refusals:
            trace.append(
                AgentTraceStep(
                    skill="audit_table_answer_consistency",
                    status="rejected",
                    summary=(
                        f"已移除 {table_refusals} 条与已验证完整表格比较矛盾的拒答；"
                        "原始生成不改写。"
                    ),
                    duration_ms=0,
                )
            )
        field_claims = (
            missing_optimizer_beta_claims(
                current_question(request.question), strong_evidence, claims,
            )
            if not request.document_ids or len(request.document_ids) == 1 else []
        )
        if field_claims:
            projected, _ = checked_claims(field_claims, strong_evidence)
            claims.extend(projected)
            trace.append(
                AgentTraceStep(
                    skill="project_explicit_optimizer_fields",
                    status="ok" if projected else "rejected",
                    summary=(
                        f"从单篇原文中补充 {len(projected)} 个明确、无冲突的 Adam β 参数；"
                        "仅抽取显式字段，不使用默认值、不改写模型输出。"
                    ),
                    duration_ms=0,
                )
            )
        norm_quotes = missing_normalization_quotes(
            current_question(request.question), strong_evidence, claims,
        )
        if norm_quotes:
            projected, _ = checked_claims(norm_quotes, strong_evidence)
            claims.extend(projected)
            trace.append(AgentTraceStep(
                skill="quote_explicit_normalization_changes",
                status="ok" if projected else "rejected",
                summary=(f"补充 {len(projected)} 条位置调整的原句摘录；"
                         "不推断未报告的起始位置或基线模型。"),
                duration_ms=0,
            ))
        missing = missing_fields(
            request.question,
            claims,
            strong_evidence,
            bleu_proof_published=table_bleu_proof_published(claims, table_claims),
        )
        missing.extend(
            f"栏目 {key}：{reason}" for key, reason in generated.field_rejections.items()
        )
        if contract_kind(request.question) or missing:
            trace.append(
                AgentTraceStep(
                    skill="audit_answer_completeness",
                    status="incomplete" if missing else "ok",
                    summary=("尚未完整回答：" + "；".join(missing))
                    if missing
                    else "请求字段通过来源及完整性规则检查；不等于独立科学审稿。",
                    duration_ms=0,
                )
            )
        source_only_refusal = only_source_limitations(claims)
        if source_only_refusal:
            trace.append(
                AgentTraceStep(
                    skill="audit_answerability",
                    status="incomplete",
                    summary="回答仅说明原文证据缺失，仍标为证据不足；有引用不等于已回答问题。",
                    duration_ms=0,
                )
            )
        answer = render_checked_claims(claims)
        if missing and claims:
            answer += "\n\n尚未完整回答：" + "；".join(missing) + "。以上仅为有据的部分结果。"
        return AskResponse(
            answer=answer,
            claims=claims,
            evidence=strong_evidence,
            insufficient_evidence=not claims or bool(missing) or source_only_refusal,
            provider=used_provider.name,
            model=used_provider.model,
            trace=trace,
            grounding_audit=grounding_audit,
        )


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
