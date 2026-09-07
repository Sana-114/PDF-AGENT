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


class DocumentStructureInput(BaseModel):
    document_id: str
    include_references: bool = False


class DocumentTableInput(BaseModel):
    document_id: str
    table_id: str | None = None


def search_evidence(
    payload: SearchEvidenceInput, context: SkillContext
) -> list[EvidenceAnchor]:
    return LexicalRetriever(context.db).search(
        question=payload.question,
        document_ids=payload.document_ids,
        top_k=payload.top_k,
    )


def get_document_outline(payload: DocumentOutlineInput, _: SkillContext) -> dict:
    parsed = _read_parsed_document(payload.document_id)
    if parsed is None:
        return {"document_id": payload.document_id, "items": [], "available": False}
    items = parsed.get("outline") or _legacy_outline(parsed)
    return {"document_id": payload.document_id, "items": items, "available": True}


def get_document_structure(payload: DocumentStructureInput, _: SkillContext) -> dict:
    parsed = _read_parsed_document(payload.document_id)
    if parsed is None:
        return {"document_id": payload.document_id, "available": False}
    result = {
        "document_id": payload.document_id,
        "available": True,
        "schema_version": parsed.get("schema_version"),
        "title": parsed.get("title"),
        "authors": parsed.get("authors", []),
        "affiliations": parsed.get("affiliations", []),
        "abstract": parsed.get("abstract"),
        "outline": parsed.get("outline") or _legacy_outline(parsed),
        "counts": {
            "pages": len(parsed.get("pages", [])),
            "tables": len(parsed.get("tables", [])),
            "figures": len(parsed.get("figures", [])),
            "formulas": len(parsed.get("formulas", [])),
            "references": len(parsed.get("references", [])),
            "appendices": len(parsed.get("appendices", [])),
        },
    }
    if payload.include_references:
        result["references"] = parsed.get("references", [])
    return result


def get_document_table(payload: DocumentTableInput, _: SkillContext) -> dict:
    parsed = _read_parsed_document(payload.document_id)
    if parsed is None:
        return {"document_id": payload.document_id, "available": False, "tables": []}
    tables = parsed.get("tables", [])
    if payload.table_id:
        tables = [table for table in tables if table.get("table_id") == payload.table_id]
    return {
        "document_id": payload.document_id,
        "available": True,
        "tables": tables,
    }


def _read_parsed_document(document_id: str) -> dict | None:
    path = Path(storage.parsed_path(document_id))
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_outline(parsed: dict) -> list[dict]:
    return [
        {
            "text": block["text"],
            "level": block.get("level") or 1,
            "page_number": page["page_number"],
            "block_id": block["block_id"],
            "bbox": block.get("bbox"),
            "children": [],
        }
        for page in parsed.get("pages", [])
        for block in page.get("blocks", [])
        if block.get("type") == "heading"
    ]


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
        SkillDefinition(
            name="get_document_structure",
            description=(
                "Return academic metadata, abstract, hierarchical outline, structure counts, "
                "and optionally anchored references for one parsed document."
            ),
            input_model=DocumentStructureInput,
            handler=get_document_structure,
        ),
        SkillDefinition(
            name="get_document_table",
            description=(
                "Return extracted table rows, cells, Markdown, captions, and page/BBox anchors."
            ),
            input_model=DocumentTableInput,
            handler=get_document_table,
        ),
    ]
    for definition in definitions:
        if definition.name not in skill_registry:
            skill_registry.register(definition)


register_builtin_skills()
