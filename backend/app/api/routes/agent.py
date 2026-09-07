from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agent.harness import ResearchAgent
from app.agent.registry import skill_registry
from app.agent.skills import register_builtin_skills
from app.core.config import settings
from app.core.database import get_db
from app.embeddings import get_embedding_provider
from app.llm import get_llm_provider
from app.llm.base import LLMConfigurationError
from app.schemas.agent import AgentStatus, AskRequest, AskResponse, SkillRead

router = APIRouter()


@router.get("/status", response_model=AgentStatus)
def get_agent_status() -> AgentStatus:
    register_builtin_skills()
    embedding = get_embedding_provider()
    try:
        provider = get_llm_provider()
        configured = provider.name == "extractive" or bool(provider.model)
        provider_name = provider.name
        model = provider.model
    except LLMConfigurationError:
        configured = False
        provider_name = settings.llm_provider
        model = settings.llm_model or None
    return AgentStatus(
        provider=provider_name,
        model=model,
        llm_configured=configured,
        retrieval_mode="hybrid_qdrant_rrf" if settings.vector_search_enabled else "lexical",
        embedding_provider=embedding.name,
        embedding_model=embedding.model,
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
