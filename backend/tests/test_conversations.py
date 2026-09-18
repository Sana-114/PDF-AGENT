import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agent.registry import SkillContext, SkillDefinition, SkillRegistry
from app.agent.skills import SearchEvidenceInput
from app.api.routes import agent as agent_routes
from app.core.database import Base, get_db
from app.llm.base import GeneratedAnswer, GeneratedClaim
from app.main import app
from app.models.chunk import DocumentChunk  # noqa: F401
from app.models.conversation import AgentConversation, AgentConversationMessage  # noqa: F401
from app.models.document import Document, DocumentStatus
from app.schemas.agent import EvidenceAnchor
from app.schemas.conversation import ConversationAskRequest, ConversationCreate
from app.services.conversations import ConversationService, ConversationValidationError


class RecordingProvider:
    name = "deepseek"
    model = "deepseek-flash"
    supports_translation = True

    def __init__(self) -> None:
        self.questions: list[str] = []

    async def generate_grounded_answer(self, question, evidence):
        self.questions.append(question)
        return GeneratedAnswer(
            answer="Adam used β1 = 0.9 and β2 = 0.98.",
            claims=[
                GeneratedClaim(
                    text="Adam used β1 = 0.9 and β2 = 0.98.",
                    evidence_ids=[evidence[0].evidence_id],
                )
            ],
        )


def _registry(queries: list[str]) -> SkillRegistry:
    registry = SkillRegistry()

    def search(payload: SearchEvidenceInput, _: SkillContext):
        queries.append(payload.question)
        return [
            EvidenceAnchor(
                evidence_id="E1",
                document_id=payload.document_ids[0] if payload.document_ids else "doc-1",
                document_title="Attention Is All You Need",
                page_number=7,
                block_ids=["p7-b5"],
                bbox=[10, 20, 300, 80],
                section="5.3 Optimizer",
                quote="Adam used β 1 = 0 . 9 and β 2 = 0 . 98.",
                score=1,
                retrieval_mode="reranked",
            )
        ]

    registry.register(
        SkillDefinition(
            name="search_evidence",
            description="Conversation test evidence.",
            input_model=SearchEvidenceInput,
            handler=search,
        )
    )
    return registry


def _add_document(session: Session) -> Document:
    document = Document(
        original_filename="1706.03762v1.pdf",
        storage_key="conversation-attention.pdf",
        size_bytes=100,
        sha256="c" * 64,
        status=DocumentStatus.READY,
        title="Attention Is All You Need",
    )
    session.add(document)
    session.commit()
    return document


@pytest.mark.asyncio
async def test_conversation_persists_turns_and_supplies_bounded_context() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    provider = RecordingProvider()
    retrieval_queries: list[str] = []
    with Session(engine, expire_on_commit=False) as session:
        document = _add_document(session)
        service = ConversationService(
            session,
            provider=provider,  # type: ignore[arg-type]
            registry=_registry(retrieval_queries),
        )
        conversation = service.create(ConversationCreate(document_ids=[document.id]))
        first = await service.ask(
            conversation.id,
            ConversationAskRequest(question="Which Adam beta values were used?"),
        )
        second = await service.ask(
            conversation.id,
            ConversationAskRequest(question="What about the second one?"),
        )
        detail = service.get(conversation.id)

        assert first.assistant_message.claims[0].evidence_ids == ["E1"]
        assert second.conversation.message_count == 4
        assert len(detail.messages) == 4
        assert detail.messages[-1].evidence[0].page_number == 7
        assert "Prior dialogue" in provider.questions[1]
        assert "Which Adam beta values were used?" in provider.questions[1]
        assert "Adam used β1 = 0.9" in provider.questions[1]
        assert "Current user question:\nWhat about the second one?" in provider.questions[1]
        assert retrieval_queries[1] == (
            "Which Adam beta values were used?\nWhat about the second one?"
        )
        assert service.list()[0].title == "Which Adam beta values were used?"

        service.delete(conversation.id)
        assert service.list() == []


def test_conversation_rejects_non_ready_document() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        with pytest.raises(ConversationValidationError, match="已就绪"):
            ConversationService(session).create(
                ConversationCreate(document_ids=["missing-document"])
            )


def test_conversation_stream_emits_status_deltas_claims_and_done(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    provider = RecordingProvider()
    queries: list[str] = []
    with Session(engine, expire_on_commit=False) as session:
        document = _add_document(session)
        service = ConversationService(
            session,
            provider=provider,  # type: ignore[arg-type]
            registry=_registry(queries),
        )
        monkeypatch.setattr(agent_routes, "ConversationService", lambda _: service)
        app.dependency_overrides[get_db] = lambda: session
        try:
            with TestClient(app) as client:
                created = client.post(
                    "/api/v1/agent/conversations",
                    json={"document_ids": [document.id]},
                )
                conversation_id = created.json()["id"]
                response = client.post(
                    f"/api/v1/agent/conversations/{conversation_id}/stream",
                    json={"question": "Which beta values were used?"},
                )
                history = client.get(
                    f"/api/v1/agent/conversations/{conversation_id}"
                ).json()
        finally:
            app.dependency_overrides.clear()

    assert created.status_code == 201
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: status" in response.text
    assert "event: delta" in response.text
    assert "event: claims" in response.text
    assert "event: evidence" in response.text
    assert "event: done" in response.text
    done_payload = next(
        json.loads(line.removeprefix("data: "))
        for block in response.text.split("\n\n")
        if block.startswith("event: done")
        for line in block.splitlines()
        if line.startswith("data: ")
    )
    assert done_payload["assistant_message"]["claims"][0]["evidence_ids"] == ["E1"]
    assert len(history["messages"]) == 2
