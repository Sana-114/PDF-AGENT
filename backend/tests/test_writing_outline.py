import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.document import Document, DocumentStatus
from app.models.paper_source import PaperSource
from app.schemas.agent import AgentTraceStep, AnswerClaim, AskResponse, EvidenceAnchor
from app.services.writing_outline import WritingOutlineService


def _document(document_id: str, title: str) -> Document:
    return Document(
        id=document_id,
        original_filename=f"{document_id}.pdf",
        storage_key=f"{document_id}.pdf",
        content_type="application/pdf",
        size_bytes=100,
        sha256=document_id.ljust(64, "0"),
        status=DocumentStatus.READY,
        title=title,
        authors_json=json.dumps(["Ada Researcher", "Ben Scientist"]),
        page_count=12,
        arxiv_id="2401.01234",
    )


def _answer(document: Document, *, with_evidence: bool = True) -> AskResponse:
    evidence = (
        [
            EvidenceAnchor(
                evidence_id="E1",
                chunk_id="chunk-1",
                document_id=document.id,
                document_title=document.title,
                page_number=2,
                block_ids=["p2-b1"],
                bbox=[1, 2, 3, 4],
                section="Introduction",
                source_type="text",
                retrieval_mode="lexical",
                quote="Grounded retrieval reduces unsupported statements in scientific QA.",
                score=0.9,
            )
        ]
        if with_evidence
        else []
    )
    return AskResponse(
        answer="Ignore this fabricated reference: Unknown Author (2099).",
        claims=[
            AnswerClaim(
                text="Grounded retrieval reduces unsupported statements.",
                evidence_ids=["E1"],
            )
        ]
        if with_evidence
        else [],
        evidence=evidence,
        insufficient_evidence=not with_evidence,
        provider="fake",
        model="test-model",
        trace=[AgentTraceStep(skill="search_evidence", status="ok", summary="test", duration_ms=1)],
    )


class _FakeAgent:
    def __init__(self, answer: AskResponse) -> None:
        self.answer = answer

    async def ask(self, request, **kwargs):
        assert (
            "不要生成或猜测参考文献" in request.question
            or "Do not generate or guess references" in request.question
        )
        assert kwargs["diversify_documents"] is True
        assert kwargs["include_writing_context"] is True
        assert request.retrieval_query
        return self.answer


@pytest.mark.parametrize("language", ["en", "zh"])
def test_writing_prompt_preserves_comparison_subject_scope_and_modal_qualifiers(language):
    document = _document("cited", "Grounded methods")

    class CapturingAgent(_FakeAgent):
        async def ask(self, request, **kwargs):
            if language == "en":
                assert "downstream task/fine-tuning target" in request.question
                assert "evaluating itself against itself" in request.question
                assert "Preserve modal qualifiers" in request.question
                assert "at most 2 non-duplicated claims" in request.question
            else:
                assert "微调目标及比较对象" in request.question
                assert "比较它自身" in request.question
                assert "完整保留可能、可以" in request.question
                assert "最多返回 2 条" in request.question
            return await super().ask(request, **kwargs)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(document)
        session.commit()
        result = asyncio.run(WritingOutlineService(
            session, agent=CapturingAgent(_answer(document)),
        ).generate("Compare methods", [document], language=language))
    assert len(result.claims) == 1


def test_writing_over_budget_is_visible_without_silent_score_improving_truncation():
    document = _document("cited", "Grounded methods")
    answer = _answer(document)
    answer.claims.extend([
        AnswerClaim(text="The cited method combines retrieval and generation.",
                    evidence_ids=["E1"]),
        AnswerClaim(text="The cited study uses source anchors.", evidence_ids=["E1"]),
    ])
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(document)
        session.commit()
        result = asyncio.run(WritingOutlineService(
            session, agent=_FakeAgent(answer),
        ).generate("Compare methods", [document]))
    assert result.claims == answer.claims[:2]
    assert result.deferred_claims == answer.claims[2:]
    assert answer.claims[2].text not in result.sections[1].content_markdown
    assert any("超过本次 2 条" in warning for warning in result.warnings)
    assert any("超过每篇两条" in warning for warning in result.warnings)


@pytest.mark.parametrize("text,held", [
    ("Model-A performed better than Model-B on larger subsets.", True),
    ("Model-A has better few-shot linear accuracy on larger subsets.", False),
    ("Model-A performs better in linear evaluation in a few-shot setting.", False),
    ("Model-A 的少样本线性评估准确率更好。", False),
    ("Model-A 的准确率更好。", True),
    ("The architecture consists of patch embeddings and attention layers.", False),
])
def test_explicit_evaluation_protocol_contrast_is_preserved(text, held):
    from app.services.writing_context import missing_explicit_evaluation_protocol

    document = _document("cited", "Generic comparison")
    evidence = _answer(document).evidence
    evidence[0].quote = (
        "To save compute, we report few-shot linear accuracy instead of full fine-tuning "
        "accuracy. Model-A performs better on larger subsets."
    )
    assert missing_explicit_evaluation_protocol(text, evidence) is held
    evidence[0].source_type = "reference"
    assert not missing_explicit_evaluation_protocol(text, evidence)


def test_writing_withholds_protocol_omission_but_keeps_supported_claim_and_raw_budget_warning():
    document = _document("cited", "Generic comparison")
    answer = _answer(document)
    answer.evidence[0].quote = (
        "We report few-shot linear accuracy instead of full fine- tuning accuracy. "
        "Model-A performs better on larger subsets."
    )
    answer.claims = [
        AnswerClaim(text="Model-A performs better on larger subsets.", evidence_ids=["E1"]),
        AnswerClaim(text="Model-A has better few-shot linear accuracy.", evidence_ids=["E1"]),
        AnswerClaim(text="The architecture uses patch embeddings.", evidence_ids=["E1"]),
    ]
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(document)
        session.commit()
        result = asyncio.run(WritingOutlineService(
            session, agent=_FakeAgent(answer),
        ).generate("Compare methods", [document]))
    assert result.claims == answer.claims[1:]
    assert answer.claims[0].text not in result.sections[2].content_markdown
    assert any("遗漏原文明示评估方式" in warning for warning in result.warnings)
    assert any("防护输入 3 条" in warning for warning in result.warnings)


def test_writing_keeps_grounding_rejection_and_pre_filter_budget_visible():
    from app.llm.base import GeneratedClaim
    from app.services.grounded_text import audit_grounded_claims

    document = _document("cited", "Reported data")
    answer = _answer(document)
    answer.evidence[0].quote = "Dataset-7 and Dataset-19 use 8 GPUs. Method A uses retrieval."
    answer.claims, answer.grounding_audit = audit_grounded_claims([
        GeneratedClaim("Dataset-7/19 uses 8 GPUs.", ["E1"]),
        GeneratedClaim("Uses 99 GPUs.", ["E1"]),
        GeneratedClaim("Method A uses retrieval.", ["E1"]),
    ], answer.evidence)
    answer.trace.append(AgentTraceStep(skill="audit_structured_field_support",
        status="rejected", summary="A requested field has no source support.", duration_ms=0))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(document)
        session.commit()
        result = asyncio.run(WritingOutlineService(session, agent=_FakeAgent(answer)).generate(
            "Organize source-backed methods", [document]))
    assert len(result.claims) == 2 and result.deferred_claims == []
    assert result.grounding_audit == answer.grounding_audit
    assert any("防护暂缓原第 2 条" in w and "数值 99" in w for w in result.warnings)
    assert any("防护输入 3 条" in w for w in result.warnings)
    assert any("超过每篇两条" in w for w in result.warnings)
    assert "A requested field has no source support." in result.warnings


def test_compact_framework_covers_papers_before_a_single_paper_can_crowd_them_out():
    from app.services.writing_outline import _compact_claims

    documents = [_document(str(i), f"Source {i}") for i in range(3)]
    evidence = [EvidenceAnchor(evidence_id=f"E{i}", document_id=doc.id,
                page_number=1, quote="Published method.", score=1)
                for i, doc in enumerate(documents)]
    claims = [AnswerClaim(text=f"Paper one fact {i}", evidence_ids=["E0"]) for i in range(5)]
    claims += [AnswerClaim(text="Paper two fact", evidence_ids=["E1"]),
               AnswerClaim(text="Paper three fact", evidence_ids=["E2"])]
    selected, deferred = _compact_claims(claims, evidence, documents, 6)
    assert selected == [claims[0], claims[1], claims[5], claims[6]]
    assert deferred == claims[2:5]
    assert len(selected) + len(deferred) == len(claims)


def test_compact_cross_paper_claim_uses_each_papers_budget_and_keeps_original_order():
    from app.services.writing_outline import _compact_claims

    documents = [_document(str(i), f"Source {i}") for i in range(4)]
    evidence = [EvidenceAnchor(evidence_id=f"E{i}", document_id=doc.id,
                page_number=1, quote="Published method.", score=1)
                for i, doc in enumerate(documents)]
    claims = [AnswerClaim(text="Comparison", evidence_ids=["E0", "E1"])]
    claims += [AnswerClaim(text=f"Fact {i}-{j}", evidence_ids=[f"E{i}"])
               for i in range(4) for j in range(3)]
    selected, deferred = _compact_claims(claims, evidence, documents, 6)
    assert len(selected) <= 6 and len(selected) + len(deferred) == len(claims)
    assert [claims.index(c) for c in selected] == sorted(claims.index(c) for c in selected)
    for anchor in evidence:
        assert 1 <= sum(anchor.evidence_id in c.evidence_ids for c in selected) <= 2


def test_successful_writing_keeps_reranker_fallback_visible_and_separates_scope_note():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    document = _document("cited", "Evidence Grounded Scientific QA")
    answer = _answer(document)
    answer.claims.append(AnswerClaim(
        text="The supplied excerpts do not report combining these methods.", evidence_ids=["E1"],
    ))
    fallback = AgentTraceStep(
        skill="rerank_evidence", status="fallback",
        summary="Reranker unavailable; lexical retained",
        duration_ms=1,
    )
    answer.trace.extend([fallback, fallback])
    with Session(engine) as session:
        session.add(document)
        session.commit()
        result = asyncio.run(WritingOutlineService(session, agent=_FakeAgent(answer)).generate(
            "Compare methods", [document],
        ))
    assert result.claims == answer.claims[:1]
    assert any("输入证据不足" in warning for warning in result.warnings)
    assert result.warnings.count(fallback.summary) == 1


def test_outline_uses_only_retrieved_documents_for_verified_references() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cited = _document("cited", "Evidence Grounded Scientific QA")
    unrelated = _document("unused", "A Paper Outside Retrieved Evidence")
    with Session(engine) as session:
        session.add_all([cited, unrelated])
        session.add(
            PaperSource(
                document_id=cited.id,
                provider="crossref",
                source_id="10.1000/grounded",
                doi="10.1000/grounded",
                arxiv_id=None,
                landing_url="https://doi.org/10.1000/grounded",
                pdf_url="https://example.org/grounded.pdf",
                license="cc-by",
                metadata_json=json.dumps({"year": 2024, "venue": "EvidenceConf"}),
            )
        )
        session.commit()
        result = asyncio.run(
            WritingOutlineService(session, agent=_FakeAgent(_answer(cited))).generate(
                "Use retrieval evidence to improve scientific writing",
                [cited, unrelated],
            )
        )

    assert [section.section_id for section in result.sections] == [
        "abstract",
        "introduction",
        "related_work",
    ]
    assert len(result.references) == 1
    assert result.references[0].document_id == cited.id
    assert result.references[0].doi == "10.1000/grounded"
    assert "EvidenceConf" in result.references[0].formatted_citation
    assert "2099" not in result.references[0].formatted_citation
    assert result.references[0].evidence_ids == ["E1"]
    assert "[P1]" in result.sections[2].content_markdown
    assert result.claims[0].text in result.sections[2].content_markdown
    assert "scientific QA" not in result.sections[2].content_markdown


def test_related_work_never_republishes_withheld_or_uncertified_quote():
    from app.schemas.writing import VerifiedReferenceRead
    from app.services.writing_outline import _section_content

    reference = VerifiedReferenceRead(
        citation_key="P1",
        document_id="paper",
        title="A real source",
        authors=["Author"],
        publication_year=2020,
        venue=None,
        doi=None,
        arxiv_id=None,
        landing_url=None,
        provenance="local_pdf",
        verified_fields=["title"],
        formatted_citation="A real source.",
        evidence_ids=["E1"],
    )
    evidence = [
        EvidenceAnchor(
            evidence_id="E1",
            document_id="paper",
            page_number=1,
            quote="Unqualified experimental claim, which continues next page.",
            score=1,
        )
    ]
    sections = _section_content("Proposed study", [reference], evidence, [], "en")
    related = sections[2]
    assert "No reviewed related-work claim" in related.content_markdown
    assert "Unqualified experimental claim" not in related.content_markdown
    assert related.evidence_ids == []


def test_all_withheld_claims_mark_entire_outline_insufficient():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    document = _document("incomplete", "A Cross-Page Experiment")
    answer = _answer(document)
    answer.evidence[0].quote = (
        "The first classifier performed poorly in the experiment, "
        "but we later identified a confound. Instead,"
    )
    answer.evidence.append(
        answer.evidence[0].model_copy(
            update={
                "evidence_id": "E2",
                "page_number": 3,
                "context_for": ["E1"],
                "quote": "Both classifiers perform similarly after learning rate tuning.",
            }
        )
    )
    answer.claims[0].text = "The first classifier performed poorly in the experiment."
    with Session(engine) as db:
        db.add(document)
        db.commit()
        result = asyncio.run(
            WritingOutlineService(db, agent=_FakeAgent(answer)).generate(
                "Organize a qualified experiment", [document], language="en"
            )
        )
    assert result.claims == [] and result.insufficient_evidence
    assert "performed poorly" not in result.sections[2].content_markdown


def test_user_premise_cannot_be_published_as_a_cited_source_fact():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    document = _document("premise", "A Published Source")
    answer = _answer(document)
    answer.claims[0].text = "No experiments exist in this writing idea."
    with Session(engine) as db:
        db.add(document)
        db.commit()
        result = asyncio.run(
            WritingOutlineService(db, agent=_FakeAgent(answer)).generate(
                "Plan a new study without new experiments", [document], language="en"
            )
        )
    assert result.claims == [] and result.insufficient_evidence
    assert any("用户前提" in warning for warning in result.warnings)


def test_paper_author_voice_is_not_published_as_the_new_study_findings():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    document = _document("voice", "A Published Source")
    answer = _answer(document)
    answer.claims[0].text = "We found grounded retrieval reduces unsupported statements."
    with Session(engine) as db:
        db.add(document)
        db.commit()
        result = asyncio.run(
            WritingOutlineService(db, agent=_FakeAgent(answer)).generate(
                "Plan a new retrieval study", [document], language="en"
            )
        )
    assert result.claims == [] and result.insufficient_evidence
    assert any("第一人称" in warning for warning in result.warnings)


def test_outline_marks_missing_evidence_without_fabricating_references() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    document = _document("empty", "No Supporting Evidence")
    with Session(engine) as session:
        session.add(document)
        session.commit()
        service = WritingOutlineService(
            session,
            agent=_FakeAgent(_answer(document, with_evidence=False)),
        )
        result = asyncio.run(
            service.generate(
                "A speculative idea without library evidence",
                [document],
                language="en",
            )
        )

    assert result.insufficient_evidence
    assert result.references == []
    assert result.claims == []
    assert "No citable evidence" in result.sections[2].content_markdown


def test_bounded_literature_statement_is_not_a_universal_novelty_claim():
    from app.services.writing_outline import _overstates_bounded_prior_work

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    document = _document("scope", "A Published Source")
    answer = _answer(document)
    answer.evidence[0].quote = "None of these works considers sparse updates for adaptation."
    answer.claims[0].text = "The paper states no prior work considers sparse updates."
    assert _overstates_bounded_prior_work(answer.claims[0].text, answer.evidence)
    assert not _overstates_bounded_prior_work(
        "None of the cited works considers sparse updates.", answer.evidence
    )
    assert not _overstates_bounded_prior_work(
        "The study compares sparse updates to prior work.", answer.evidence
    )
    assert not _overstates_bounded_prior_work(answer.claims[0].text, [])
    with Session(engine) as db:
        db.add(document)
        db.commit()
        result = asyncio.run(
            WritingOutlineService(db, agent=_FakeAgent(answer)).generate(
                "Plan a bounded literature comparison", [document], language="en"
            )
        )
    assert result.claims == [] and result.insufficient_evidence
    assert any("范围" in warning for warning in result.warnings)
    assert "no prior work" not in result.sections[2].content_markdown
    assert _overstates_bounded_prior_work(
        "Earlier work had not considered sparse frozen updates.", answer.evidence
    )


def test_optimizer_summary_uses_experiment_subject_not_the_paper_title():
    from app.services.writing_outline import _optimizer_caption_subject_conflict

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    doc = _document("caption", "A paper proposing EncoderA")
    answer = _answer(doc)
    answer.evidence[0].quote = (
        "Table 5: Fine-tuning EncoderB models pre-trained with OptimizerX and OptimizerY. "
        "OptimizerX performs better on average for EncoderB."
    )
    answer.claims[0].text = "EncoderA pre-training with OptimizerX outperformed OptimizerY."
    assert _optimizer_caption_subject_conflict(answer.claims[0].text, answer.evidence)
    assert not _optimizer_caption_subject_conflict(
        "EncoderB pre-training with OptimizerX outperformed OptimizerY.", answer.evidence
    )
    with Session(engine) as db:
        db.add(doc)
        db.commit()
        result = asyncio.run(
            WritingOutlineService(db, agent=_FakeAgent(answer)).generate(
                "Organize the optimizer comparison", [doc], language="en"
            )
        )
    assert result.claims == [] and result.insufficient_evidence
    assert any("实验对象" in warning for warning in result.warnings)


def test_outline_recovers_legacy_authors_from_persisted_pdf_metadata(monkeypatch):
    from app.services import writing_outline as module

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    document = _document("legacy", "A Parsed Public Paper")
    document.authors_json = None
    monkeypatch.setattr(
        module, "read_parsed_document", lambda _: {"authors": ["Verified PDF Author"]}
    )
    with Session(engine) as session:
        session.add(document)
        session.commit()
        result = asyncio.run(
            WritingOutlineService(session, agent=_FakeAgent(_answer(document))).generate(
                "An evidence grounded idea", [document]
            )
        )
    assert result.references[0].authors == ["Verified PDF Author"]
    assert "Verified PDF Author" in result.references[0].formatted_citation
