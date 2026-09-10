from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agent.harness import ResearchAgent
from app.agent.registry import skill_registry
from app.agent.skills import register_builtin_skills
from app.core.config import settings
from app.core.database import get_db
from app.embeddings import get_embedding_provider
from app.llm import get_llm_provider
from app.llm.base import LLMConfigurationError, LLMResponseError
from app.rerankers import get_reranker
from app.schemas.agent import (
    AgentStatus,
    AskRequest,
    AskResponse,
    SkillRead,
    TranslateRequest,
    TranslateResponse,
)

router = APIRouter()


@router.get("/status", response_model=AgentStatus)
def get_agent_status() -> AgentStatus:
    register_builtin_skills()
    embedding = get_embedding_provider()
    reranker = get_reranker()
    try:
        provider = get_llm_provider()
        configured = provider.name == "extractive" or bool(provider.model)
        translation_configured = provider.supports_translation and bool(provider.model)
        provider_name = provider.name
        model = provider.model
    except LLMConfigurationError:
        configured = False
        translation_configured = False
        provider_name = settings.llm_provider
        model = settings.llm_model or None
    retrieval_mode = "hybrid_qdrant_rrf" if settings.vector_search_enabled else "lexical"
    if reranker.enabled:
        retrieval_mode = f"{retrieval_mode}+cross_encoder"
    return AgentStatus(
        provider=provider_name,
        model=model,
        llm_configured=configured,
        translation_configured=translation_configured,
        retrieval_mode=retrieval_mode,
        embedding_provider=embedding.name,
        embedding_model=embedding.model,
        reranker_provider=reranker.name,
        reranker_model=reranker.model or None,
        skills=[item.name for item in skill_registry.list()],
    )


@router.get("/skills", response_model=list[SkillRead])
def list_agent_skills() -> list[SkillRead]:
    register_builtin_skills()
    return [SkillRead.model_validate(item.public_dict()) for item in skill_registry.list()]


@router.post("/ask", response_model=AskResponse)
async def ask_agent(
    request: AskRequest, db: Annotated[Session, Depends(get_db)]
) -> AskResponse:
    agent = ResearchAgent(db)
    return await agent.ask(request)


@router.post("/translate", response_model=TranslateResponse)
async def translate_selection(request: TranslateRequest) -> TranslateResponse:
    try:
        provider = get_llm_provider()
        result = await provider.translate_text(
            request.text,
            request.source_language,
            request.target_language,
        )
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return TranslateResponse(
        translation=result.text,
        source_language=request.source_language,
        target_language=request.target_language,
        provider=provider.name,
        model=provider.model,
    )
