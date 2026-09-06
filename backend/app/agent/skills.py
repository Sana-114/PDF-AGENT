import json
from pathlib import Path

from pydantic import BaseModel, Field

from app.agent.registry import SkillContext, SkillDefinition, skill_registry
from app.schemas.agent import EvidenceAnchor
from app.services.retrieval import LexicalRetriever
from app.services.storage import storage


class SearchEvidenceInput(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    document_ids: list[str] | None = None
    top_k: int = Field(default=6, ge=1, le=20)


class DocumentOutlineInput(BaseModel):
    document_id: str


def search_evidence(
    payload: SearchEvidenceInput, context: SkillContext
) -> list[EvidenceAnchor]:
    return LexicalRetriever(context.db).search(
        question=payload.question,
        document_ids=payload.document_ids,
        top_k=payload.top_k,
    )


def get_document_outline(payload: DocumentOutlineInput, _: SkillContext) -> dict:
    path = Path(storage.parsed_path(payload.document_id))
    if not path.exists():
        return {"document_id": payload.document_id, "items": [], "available": False}
    parsed = json.loads(path.read_text(encoding="utf-8"))
    items = [
        {
            "text": block["text"],
            "level": block.get("level") or 1,
            "page_number": page["page_number"],
            "block_id": block["block_id"],
        }
        for page in parsed.get("pages", [])
        for block in page.get("blocks", [])
        if block.get("type") == "heading"
    ]
    return {"document_id": payload.document_id, "items": items, "available": True}


def register_builtin_skills() -> None:
    definitions = [
        SkillDefinition(
            name="search_evidence",
            description=(
                "Search parsed PDF chunks and return ranked verbatim passages with document, "
                "page, block, and bounding-box anchors."
            ),
            input_model=SearchEvidenceInput,
            handler=search_evidence,
        ),
        SkillDefinition(
            name="get_document_outline",
            description="Return the extracted heading outline and jump anchors for one document.",
            input_model=DocumentOutlineInput,
            handler=get_document_outline,
        ),
    ]
    for definition in definitions:
        if definition.name not in skill_registry:
            skill_registry.register(definition)


register_builtin_skills()

