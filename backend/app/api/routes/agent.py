import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
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
from app.schemas.conversation import (
    ConversationAskRequest,
    ConversationCreate,
    ConversationDetail,
    ConversationSummary,
    ConversationTurnRead,
)
from app.services.conversations import (
    ConversationNotFoundError,
    ConversationService,
    ConversationValidationError,
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


@router.post(
    "/conversations",
    response_model=ConversationDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    request: ConversationCreate,
    db: Annotated[Session, Depends(get_db)],
) -> ConversationDetail:
    try:
        return ConversationService(db).create(request)
    except ConversationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(
    db: Annotated[Session, Depends(get_db)],
) -> list[ConversationSummary]:
    return ConversationService(db).list()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> ConversationDetail:
    try:
        return ConversationService(db).get(conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_conversation(
    conversation_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    try:
        ConversationService(db).delete(conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationTurnRead,
)
async def ask_conversation(
    conversation_id: str,
    request: ConversationAskRequest,
    db: Annotated[Session, Depends(get_db)],
) -> ConversationTurnRead:
    service = ConversationService(db)
    try:
        return await service.ask(conversation_id, request)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/conversations/{conversation_id}/stream")
async def stream_conversation(
    conversation_id: str,
    request: ConversationAskRequest,
    db: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    service = ConversationService(db)
    try:
        service.get(conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def events() -> AsyncIterator[str]:
        yield _sse_event(
            "status",
            {"phase": "retrieving", "message": "正在检索原文并生成受证据约束的回答…"},
        )
        try:
            turn = await service.ask(conversation_id, request)
        except Exception:
            db.rollback()
            yield _sse_event(
                "error",
                {"message": "问答生成失败，请稍后重试。"},
            )
            return

        yield _sse_event(
            "message_start",
            {
                "message_id": turn.assistant_message.id,
                "provider": turn.assistant_message.provider,
                "model": turn.assistant_message.model,
                "insufficient_evidence": turn.assistant_message.insufficient_evidence,
            },
        )
        answer = turn.assistant_message.content
        for offset in range(0, len(answer), 24):
            yield _sse_event("delta", {"text": answer[offset : offset + 24]})
            await asyncio.sleep(0)
        yield _sse_event(
            "claims",
            {"items": [item.model_dump(mode="json") for item in turn.assistant_message.claims]},
        )
        yield _sse_event(
            "evidence",
            {
                "items": [
                    item.model_dump(mode="json") for item in turn.assistant_message.evidence
                ]
            },
        )
        yield _sse_event(
            "trace",
            {"items": [item.model_dump(mode="json") for item in turn.assistant_message.trace]},
        )
        yield _sse_event("done", turn.model_dump(mode="json"))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


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
