import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.llm.base import GeneratedAnswer, GeneratedClaim
from app.llm.extractive import ExtractiveProvider
from app.models.document import Document, DocumentStatus
from app.services.evidence_review import EvidenceReviewService


def _document(document_id: str, title: str, arxiv_id: str | None) -> Document:
    return Document(
        id=document_id,
        original_filename=f"{document_id}.pdf",
        storage_key=f"{document_id}.pdf",
        content_type="application/pdf",
        size_bytes=100,
        sha256=document_id.ljust(64, "0"),
        status=DocumentStatus.READY,
        title=title,
        authors_json=json.dumps([f"Author {document_id.upper()}"]),
        page_count=8,
        arxiv_id=arxiv_id,
    )


def _block(block_id: str, text: str, *, kind: str = "text", level: int | None = None):
    payload = {
        "block_id": block_id,
        "text": text,
        "type": kind,
        "bbox": [10, 20, 500, 60],
    }
    if level:
        payload["level"] = level
    return payload


def _write_parsed(storage_module, document_id: str, pages: list[dict], references=None):
    storage_module.storage.parsed_path(document_id).write_text(
        json.dumps(
            {
                "title": document_id,
                "pages": pages,
                "references": references or [],
            }
        ),
        encoding="utf-8",
    )


def test_future_work_is_flagged_only_with_later_progress_evidence(tmp_path, monkeypatch) -> None:
    from app.services import storage as storage_module

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(storage_module.settings, "parsed_dir", parsed_dir)
    old = _document("old", "Retrieval for Text Documents", "1701.00001")
    new = _document("new", "Multimodal Document Retrieval", "2301.00001")
    _write_parsed(
        storage_module,
        old.id,
        [
            {
                "page_number": 1,
                "blocks": [
                    _block("o-h1", "Abstract", kind="heading", level=1),
                    _block("o-a1", "We present a retrieval method for text documents."),
                ],
            },
            {
                "page_number": 8,
                "blocks": [
                    _block("o-h2", "Future Work", kind="heading", level=1),
                    _block(
                        "o-f1",
                        "Future work should extend retrieval to multimodal documents.",
                    ),
                ],
            },
        ],
    )
    _write_parsed(
        storage_module,
        new.id,
        [
            {
                "page_number": 1,
                "blocks": [
                    _block("n-h1", "Abstract", kind="heading", level=1),
                    _block("n-a0", "We evaluate a method for image classification."),
                    _block("n-a1", "We present retrieval for multimodal documents."),
                ],
            }
        ],
        references=[
            {
                "reference_id": "ref-1",
                "label": "1",
                "text": "Retrieval for Text Documents. 2017.",
                "page_number": 7,
                "block_ids": ["n-r1"],
            }
        ],
    )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([old, new])
        session.commit()
        result = asyncio.run(
            EvidenceReviewService(session, provider=ExtractiveProvider()).generate([old, new])
        )
        low = asyncio.run(
            EvidenceReviewService(session, provider=ExtractiveProvider()).generate(
                [old, new], max_evidence=2
            )
        )

    assert result.graph_stats.relation_count == 1
    assert len(result.future_directions) == 1
    direction = result.future_directions[0]
    assert direction.status == "possibly_addressed"
    assert direction.possibly_addressed_by_document_ids == [new.id]
    assert direction.progress_evidence_ids
    assert direction.exploration_score < 0.3
    assert direction.source_evidence_id in {e.evidence_id for e in result.future_work_evidence}
    assert low.future_directions == result.future_directions
    assert all("multimodal" not in e.quote for e in low.evidence)


def test_future_candidates_are_independent_of_generation_budget(tmp_path, monkeypatch):
    from app.services import storage as storage_module

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(storage_module.settings, "parsed_dir", parsed_dir)
    docs = [_document(key, f"Source {key}", "2301.00001") for key in ("a", "b")]
    for doc in docs:
        _write_parsed(
            storage_module,
            doc.id,
            [
                {
                    "page_number": 1,
                    "blocks": [
                        _block(doc.id + "-h", "Abstract", kind="heading"),
                        _block(doc.id + "-a", "A supported overview of document retrieval."),
                        _block(
                            doc.id + "-f1", "Future work should evaluate multilingual retrieval."
                        ),
                        _block(doc.id + "-f2", "We plan to study robust retrieval under noise."),
                    ],
                }
            ],
        )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(docs)
        db.commit()
        low = asyncio.run(
            EvidenceReviewService(db, provider=ExtractiveProvider()).generate(docs, max_evidence=1)
        )
        high = asyncio.run(
            EvidenceReviewService(db, provider=ExtractiveProvider()).generate(docs, max_evidence=10)
        )
    assert len(low.evidence) == 1 and len(high.evidence) == 6
    assert len(low.future_work_evidence) == 6
    assert low.future_work_evidence == high.future_work_evidence
    assert {d.direction_id for d in low.future_directions} == {
        d.direction_id for d in high.future_directions
    }
    assert all(p.future_work_evidence_ids for p in low.papers)


def test_local_only_future_ids_cannot_support_generated_claims(tmp_path, monkeypatch):
    from app.services import storage as storage_module

    class Provider:
        name, model = "fake", "test"

        async def generate_grounded_answer(self, question, evidence):
            assert [e.evidence_id for e in evidence] == ["R1"]
            assert "Future work should evaluate multilingual retrieval" not in question
            assert '"R1": "overview"' in question
            assert "作者计划不能改写成已经实现的功能" in question
            assert "只比较各自机制" in question
            return GeneratedAnswer(
                answer="",
                claims=[
                    GeneratedClaim(text="A generated unsupported direction.", evidence_ids=["F1"])
                ],
            )

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(storage_module.settings, "parsed_dir", parsed_dir)
    doc = _document("future", "Future Source", "2301.00001")
    _write_parsed(
        storage_module,
        doc.id,
        [
            {
                "page_number": 1,
                "blocks": [
                    _block("h", "Abstract", kind="heading"),
                    _block("a", "An overview of document retrieval."),
                    _block("f", "Future work should evaluate multilingual retrieval."),
                ],
            }
        ],
    )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(doc)
        db.commit()
        result = asyncio.run(
            EvidenceReviewService(db, provider=Provider()).generate([doc], max_evidence=1)
        )
    assert len(result.future_directions) == 1
    assert len(result.future_work_evidence) == 2
    assert result.claims == [] and result.insufficient_evidence


def test_old_review_schema_defaults_to_no_extra_local_evidence():
    from app.schemas.review import ResearchReviewRead

    assert ResearchReviewRead.model_fields["future_work_evidence"].default_factory() == []


def test_future_extraction_excludes_contents_background_and_bibliography():
    from app.services.evidence_review import _extract_drafts

    doc = _document("scope", "Scoped Source", "2301.00001")
    blocks = [
        _block("h1", "Introduction", kind="heading"),
        _block("old", "The major limitation is that standard models are unidirectional."),
        _block("toc", "5 Limitations 33"),
        _block("cite", "Future work on retrieval. A cited paper, 2020."),
        _block("h2", "Future Work", kind="heading"),
        _block("own", "Our method needs evaluation on multilingual documents."),
        _block("h3", "References", kind="heading"),
        _block("refs", "We hope to enable future research. A bibliography entry."),
        _block("h4", "Appendix", kind="heading"),
        _block("real", "We plan to study multilingual document retrieval."),
    ]
    parsed = {
        "pages": [{"page_number": 1, "blocks": blocks}],
        "references": [{"block_ids": ["cite"]}],
    }
    future = [d for d in _extract_drafts(doc, parsed, 2023) if d.kind == "future_work"]
    assert [d.block_ids for d in future] == [["own"], ["real"]]


def test_long_paragraph_keeps_late_future_sentence_and_literal_source():
    from app.services.evidence_review import _direction_sentence, _extract_drafts

    doc = _document("long", "A Long Paragraph", "2301.00001")
    text = "Existing architecture details are explained here. " * 70
    text += "Future work should evaluate multilingual document retrieval."
    parsed = {"pages": [{"page_number": 1, "blocks": [_block("source", text)]}]}
    future = [d for d in _extract_drafts(doc, parsed, 2023) if d.kind == "future_work"]
    assert len(future) == 1
    assert future[0].quote in text
    assert future[0].quote.startswith("Future work")
    assert "multilingual" in _direction_sentence(future[0].quote)


def test_mixed_conclusion_retains_actual_future_not_completed_summary_or_running_title():
    from app.services.evidence_review import _direction_sentence, _extract_drafts

    doc = _document("mixed", "A Study of Normalization", "2002.00001")
    body = ("In this paper we show the result at initialization. "
            "In the future, we will investigate other positioning strategies.")
    parsed = {"pages": [{"page_number": 8, "blocks": [
        _block("h", "5. Conclusion and Future Work", kind="heading"),
        _block("completed", "We show that the model converges faster."),
        _block("plan", body),
    ]}, {"page_number": 9, "blocks": [
        _block("running", "A Study of Normalization"),
        _block("footer", "Future Work Journal", kind="footer"),
    ]}]}
    drafts = [d for d in _extract_drafts(doc, parsed, 2020) if d.kind == "future_work"]
    assert [d.block_ids for d in drafts] == [["plan"]]
    assert _direction_sentence(drafts[0].quote) == (
        "In the future, we will investigate other positioning strategies.")
    assert drafts[0].quote in body


def test_direction_sentence_prefers_open_plan_not_current_experiment_or_artifact_release():
    from app.services.evidence_review import _direction_sentence, _extract_drafts

    experiment = "We further study attention weights in Section 7.1. "
    plan = "We leave the investigation of adapting other layers to a future work."
    assert _direction_sentence(experiment + plan) == plan
    doc = _document("narrative", "Adaptation Study", "2106.00001")
    parsed = {"pages": [{"page_number": 5, "blocks": [
        _block("h", "5.4 Experiments", kind="heading"),
        _block("study", "We hope to answer if this works on generation. We present our results."),
        _block("release", "We release all model generations to facilitate further study."),
        _block("source", experiment + plan),
    ]}]}
    drafts = [d for d in _extract_drafts(doc, parsed, 2021) if d.kind == "future_work"]
    assert [d.block_ids for d in drafts] == [["source"]]


def test_genuine_hope_is_retained_without_a_literal_future_heading():
    from app.services.evidence_review import _extract_drafts

    doc = _document("hope", "An Adaptation Study", "2106.00001")
    text = ("Given the empirical advantage, we hope to further explain "
            "the learned low-rank properties.")
    parsed = {"pages": [{"page_number": 10, "blocks": [
        _block("h", "Conclusion", kind="heading"), _block("plan", text),
    ]}]}
    future = [d for d in _extract_drafts(doc, parsed, 2021) if d.kind == "future_work"]
    assert [d.quote for d in future] == [text]


def test_ocr_decorated_abstract_and_numbered_overview_do_not_fall_back_to_credits():
    from app.services.evidence_review import _extract_drafts

    doc = _document("ocr", "Scanned Model", "1706.00001")
    body = ("We present a new attention mechanism for sequence transduction "
            "and evaluate it carefully.")
    for heading in ("® Abstract", "1 Introduction", "5. Conclusion"):
        parsed = {"pages": [{"page_number": 1, "blocks": [
            _block("authors", "Authors and institutions " * 8 + "author@example.org"),
            _block("h", heading, kind="heading"), _block("body", body),
        ]}]}
        drafts = [d for d in _extract_drafts(doc, parsed, 2017) if d.kind == "overview"]
        assert [d.block_ids for d in drafts] == [["body"]]
    parsed["pages"][0]["blocks"] = parsed["pages"][0]["blocks"][:1]
    assert not _extract_drafts(doc, parsed, 2017)


def test_review_discards_claims_without_valid_evidence_ids(tmp_path, monkeypatch) -> None:
    from app.services import storage as storage_module

    class FakeProvider:
        name = "fake"
        model = "grounded-test"
        supports_translation = False

        async def generate_grounded_answer(self, question, evidence):
            assert "领域综述" in question
            assert evidence[0].evidence_id == "R1"
            return GeneratedAnswer(
                answer="Evidence-grounded review.",
                claims=[
                    GeneratedClaim(text="Supported", evidence_ids=["R1", "invented"]),
                    GeneratedClaim(text="Unsupported", evidence_ids=["invented"]),
                ],
            )

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(storage_module.settings, "parsed_dir", parsed_dir)
    document = _document("paper", "Grounded Review Systems", None)
    _write_parsed(
        storage_module,
        document.id,
        [
            {
                "page_number": 1,
                "blocks": [
                    _block("p-h1", "Abstract", kind="heading", level=1),
                    _block(
                        "p-a1",
                        "We present an evidence-grounded review system with stable anchors.",
                    ),
                ],
            }
        ],
    )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(document)
        session.commit()
        result = asyncio.run(
            EvidenceReviewService(session, provider=FakeProvider()).generate([document])
        )

    assert result.review == "Supported [R1]"
    assert result.provider == "fake"
    assert [claim.text for claim in result.claims] == ["Supported"]
    assert result.claims[0].evidence_ids == ["R1"]
    assert not result.insufficient_evidence
    assert result.grounding_audit.input_claim_count == 2
    assert result.grounding_audit.accepted_claim_count == 1
    assert result.grounding_audit.rejections[0].claim_index == 1
    assert any("防护暂缓原第 2 条" in warning for warning in result.warnings)
