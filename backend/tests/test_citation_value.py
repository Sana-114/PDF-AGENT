import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes import discovery
from app.core.database import Base
from app.models.document import Document, DocumentStatus
from app.schemas.discovery import ReferenceResolution, ReferenceResolveRequest
from app.services.citation_value import citation_values


def _parsed(text, labels=("1", "2")):
    return {
        "references": [
            {
                "reference_id": f"ref-{label}",
                "label": label,
                "text": "Bibliography",
                "page_number": 9,
                "block_ids": ["refs"],
            }
            for label in labels
        ],
        "pages": [
            {
                "page_number": 2,
                "blocks": [
                    {"block_id": "body", "text": text, "bbox": [1, 2, 3, 4]},
                    {"block_id": "refs", "text": "We adopt a method [2]."},
                ],
            }
        ],
    }


def test_each_reference_uses_its_own_sentence_not_the_whole_block():
    values = citation_values(_parsed("We adopt the method [1]. For an overview see [2]."))
    assert values["ref-1"].role == "method"
    assert values["ref-2"].role == "incidental"
    context = values["ref-2"].contexts[0]
    assert context.quote == "For an overview see [2]."
    assert context.page_number == 2 and context.block_id == "body"
    assert context.bbox == [1, 2, 3, 4]


def test_missing_or_negated_evidence_not_auto_recommended():
    values = citation_values(_parsed("We do not use the model [1]."))
    assert values["ref-1"].role == "unknown"
    assert not values["ref-1"].recommended
    assert values["ref-2"].mention_count == 0
    assert not values["ref-2"].recommended


def test_substantive_use_overrides_incidental_mention():
    values = citation_values(_parsed("See also [1]. We compare against [1] as a baseline."))
    assert values["ref-1"].role == "baseline"
    assert values["ref-1"].recommended
    assert values["ref-1"].mention_count == 2
    assert values["ref-1"].contexts[0].role == "baseline"


def test_background_and_grouped_method_uses():
    values = citation_values(_parsed("Prior work [1] introduced this task."))
    assert values["ref-1"].role == "background"
    assert not values["ref-1"].recommended
    grouped = citation_values(_parsed("We extend methods [1, 2]."))
    assert all(value.recommended for value in grouped.values())


def test_long_ocr_run_on_does_not_attribute_unrelated_use():
    values = citation_values(_parsed("We adopt " + "text " * 400 + "[1]"))
    assert values["ref-1"].role == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,ids", [("recommended", ["ref-2"]), ("all", ["ref-1", "ref-2"])])
async def test_endpoint_filters_before_search_and_explicit_ids_override(
    tmp_path, monkeypatch, mode, ids
):
    parsed = _parsed("See also [1]. We adopt the model [2].")
    monkeypatch.setattr(discovery, "read_parsed_document", lambda _: parsed)

    class Resolver:
        async def resolve_many(self, references, **kwargs):
            return [
                ReferenceResolution(
                    reference_id=ref["reference_id"],
                    label=ref["label"],
                    text=ref["text"],
                    page_number=9,
                    status="not_found",
                )
                for ref in references
            ]

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            Document(
                id="value-test",
                original_filename="test.pdf",
                storage_key="test.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="c" * 64,
                status=DocumentStatus.READY,
            )
        )
        db.commit()
        response = await discovery.resolve_document_references(
            ReferenceResolveRequest(document_id="value-test", selection_mode=mode), db, Resolver()
        )
        assert [item.reference_id for item in response.items] == ids
        assert response.excluded_incidental == (1 if mode == "recommended" else 0)
        explicit = await discovery.resolve_document_references(
            ReferenceResolveRequest(document_id="value-test", reference_ids=["ref-1"]),
            db,
            Resolver(),
        )
        assert explicit.attempted == 1 and explicit.excluded_incidental == 0
        assert explicit.items[0].citation_value.role == "incidental"
