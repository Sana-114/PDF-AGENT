from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.agent.harness import ResearchAgent
from app.agent.registry import SkillRegistry
from app.llm.base import LLMProvider
from app.models.conversation import AgentConversation, AgentConversationMessage
from app.models.document import Document, DocumentStatus
from app.schemas.agent import AskRequest, AskResponse
from app.schemas.conversation import (
    ConversationAskRequest,
    ConversationCreate,
    ConversationDetail,
    ConversationMessageRead,
    ConversationSummary,
    ConversationTurnRead,
)

CONTEXT_MESSAGE_LIMIT = 6
CONTEXT_CHARACTER_LIMIT = 6000


class ConversationNotFoundError(LookupError):
    pass


class ConversationValidationError(ValueError):
    pass


class ConversationService:
    def __init__(
        self,
        session: Session,
        *,
        provider: LLMProvider | None = None,
        registry: SkillRegistry | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.registry = registry

    def create(self, payload: ConversationCreate) -> ConversationDetail:
        self._validate_documents(payload.document_ids)
        conversation = AgentConversation(
            title=payload.title or "新对话",
            document_ids_json=json.dumps(payload.document_ids),
        )
        self.session.add(conversation)
        self.session.commit()
        return self.get(conversation.id)

    def list(self) -> list[ConversationSummary]:
        rows = self.session.execute(
            select(AgentConversation, func.count(AgentConversationMessage.id))
            .outerjoin(
                AgentConversationMessage,
                AgentConversationMessage.conversation_id == AgentConversation.id,
            )
            .group_by(AgentConversation.id)
            .order_by(AgentConversation.updated_at.desc(), AgentConversation.id)
        ).all()
        return [self._summary(conversation, int(count)) for conversation, count in rows]

    def get(self, conversation_id: str) -> ConversationDetail:
        conversation = self._get_model(conversation_id)
        messages = self.session.scalars(
            select(AgentConversationMessage)
            .where(AgentConversationMessage.conversation_id == conversation_id)
            .order_by(AgentConversationMessage.sequence, AgentConversationMessage.created_at)
        ).all()
        summary = self._summary(conversation, len(messages))
        return ConversationDetail(
            **summary.model_dump(),
            messages=[self._message(item) for item in messages],
        )

    def delete(self, conversation_id: str) -> None:
        conversation = self._get_model(conversation_id)
        self.session.execute(
            delete(AgentConversationMessage).where(
                AgentConversationMessage.conversation_id == conversation_id
            )
        )
        self.session.delete(conversation)
        self.session.commit()

    async def ask(
        self,
        conversation_id: str,
        payload: ConversationAskRequest,
    ) -> ConversationTurnRead:
        conversation = self._get_model(conversation_id)
        previous = self.session.scalars(
            select(AgentConversationMessage)
            .where(AgentConversationMessage.conversation_id == conversation_id)
            .order_by(AgentConversationMessage.sequence.desc())
            .limit(CONTEXT_MESSAGE_LIMIT)
        ).all()
        previous = list(reversed(previous))
        contextual_question = _contextual_question(payload.question, previous)
        retrieval_query = _retrieval_query(payload.question, previous)
        document_ids = json.loads(conversation.document_ids_json)

        agent_kwargs = {"provider": self.provider} if self.provider is not None else {}
        if self.registry is not None:
            agent_kwargs["registry"] = self.registry
        response = await ResearchAgent(self.session, **agent_kwargs).ask(
            AskRequest(
                question=contextual_question,
                retrieval_query=retrieval_query,
                document_ids=document_ids or None,
                top_k=payload.top_k,
            )
        )

        next_sequence = (previous[-1].sequence + 1) if previous else 1
        user_message = AgentConversationMessage(
            conversation_id=conversation.id,
            sequence=next_sequence,
            role="user",
            content=payload.question,
        )
        assistant_message = _assistant_message(
            conversation.id,
            next_sequence + 1,
            response,
        )
        if conversation.title == "新对话":
            conversation.title = _conversation_title(payload.question)
        conversation.updated_at = datetime.now(UTC)
        self.session.add_all([user_message, assistant_message])
        self.session.commit()
        return ConversationTurnRead(
            conversation=self._summary(conversation, next_sequence + 1),
            user_message=self._message(user_message),
            assistant_message=self._message(assistant_message),
        )

    def _get_model(self, conversation_id: str) -> AgentConversation:
        conversation = self.session.get(AgentConversation, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError("问答会话不存在。")
        return conversation

    def _validate_documents(self, document_ids: list[str]) -> None:
        if not document_ids:
            return
        ready_ids = set(
            self.session.scalars(
                select(Document.id).where(
                    Document.id.in_(document_ids),
                    Document.status == DocumentStatus.READY,
                )
            ).all()
        )
        missing = [document_id for document_id in document_ids if document_id not in ready_ids]
        if missing:
            raise ConversationValidationError(
                "会话只能关联已就绪文献；无效文献：" + ", ".join(missing)
            )

    @staticmethod
    def _summary(
        conversation: AgentConversation,
        message_count: int,
    ) -> ConversationSummary:
        return ConversationSummary(
            id=conversation.id,
            title=conversation.title,
            document_ids=json.loads(conversation.document_ids_json),
            message_count=message_count,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )

    @staticmethod
    def _message(message: AgentConversationMessage) -> ConversationMessageRead:
        return ConversationMessageRead(
            id=message.id,
            conversation_id=message.conversation_id,
            sequence=message.sequence,
            role=message.role,
            content=message.content,
            claims=json.loads(message.claims_json),
            evidence=json.loads(message.evidence_json),
            trace=json.loads(message.trace_json),
            insufficient_evidence=message.insufficient_evidence,
            provider=message.provider,
            model=message.model,
            created_at=message.created_at,
        )


def _assistant_message(
    conversation_id: str,
    sequence: int,
    response: AskResponse,
) -> AgentConversationMessage:
    return AgentConversationMessage(
        conversation_id=conversation_id,
        sequence=sequence,
        role="assistant",
        content=response.answer,
        claims_json=json.dumps(
            [item.model_dump(mode="json") for item in response.claims],
            ensure_ascii=False,
        ),
        evidence_json=json.dumps(
            [item.model_dump(mode="json") for item in response.evidence],
            ensure_ascii=False,
        ),
        trace_json=json.dumps(
            [item.model_dump(mode="json") for item in response.trace],
            ensure_ascii=False,
        ),
        insufficient_evidence=response.insufficient_evidence,
        provider=response.provider,
        model=response.model,
    )


def _contextual_question(
    question: str,
    messages: list[AgentConversationMessage],
) -> str:
    if not messages:
        return question
    rendered = []
    characters = 0
    for message in reversed(messages):
        label = "User" if message.role == "user" else "Assistant"
        line = f"{label}: {message.content[:1200]}"
        if characters + len(line) > CONTEXT_CHARACTER_LIMIT:
            break
        rendered.append(line)
        characters += len(line)
    rendered.reverse()
    return (
        "Prior dialogue is context only, never factual evidence. Continue the conversation while "
        "supporting every factual claim exclusively with the retrieved PDF evidence.\n\n"
        "Prior dialogue:\n"
        + "\n".join(rendered)
        + f"\n\nCurrent user question:\n{question}"
    )


def _retrieval_query(
    question: str,
    messages: list[AgentConversationMessage],
) -> str:
    previous_questions = [
        message.content[:1200]
        for message in messages
        if message.role == "user"
    ][-2:]
    return "\n".join([*previous_questions, question])


def _conversation_title(question: str) -> str:
    single_line = " ".join(question.split())
    return single_line if len(single_line) <= 60 else single_line[:57] + "..."
