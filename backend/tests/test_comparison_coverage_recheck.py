from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.llm.base import GeneratedAnswer, GeneratedClaim
from app.models.document import Document, DocumentStatus
from app.schemas.agent import EvidenceAnchor
from app.schemas.comparison import ComparisonPaperRead
from app.services.paper_comparison import (
    PaperComparisonService,
    _comparison_facets,
    _missing_generated_source_papers,
)
from scripts.evaluate_real_pdf_comparison import (
    _check_cross_document_fact,
    _check_fact,
    _select_evaluation_cases,
    regrade_report,
)


class CoverageRetriever:
    def search(self, question, document_ids=None, top_k=6):
        return [EvidenceAnchor(
            evidence_id="local", document_id=document_ids[0], page_number=2,
            quote="The model uses layer normalization.", score=0.95,
            section="Model", block_ids=[document_ids[0]], bbox=[10, 10, 100, 100],
            retrieval_mode="reranked",
        )]


class OneSidedThenCompleteProvider:
    name = "deepseek"
    model = "deepseek-flash"
    supports_translation = True

    def __init__(self, *, remain_partial=False):
        self.prompts = []
        self.remain_partial = remain_partial

    async def generate_grounded_answer(self, question, evidence):
        self.prompts.append(question)
        if len(self.prompts) == 1 or self.remain_partial:
            claims = [GeneratedClaim(
                text="[SINGLE_SOURCE][DIMENSION: normalization] Paper A uses layer normalization.",
                evidence_ids=["E1"],
            )]
        else:
            claims = [GeneratedClaim(
                text="[AGREEMENT][DIMENSION: normalization] Both papers use layer normalization.",
                evidence_ids=["E1", "E2"],
            )]
        return GeneratedAnswer(answer="grounded", claims=claims)


@pytest.mark.asyncio
@pytest.mark.parametrize("remain_partial", [False, True])
async def test_one_source_omission_is_rechecked_only_once(remain_partial):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    provider = OneSidedThenCompleteProvider(remain_partial=remain_partial)
    with Session(engine, expire_on_commit=False) as session:
        documents = [Document(
            original_filename=f"coverage-{index}.pdf", storage_key=f"coverage-{index}.pdf",
            size_bytes=100, sha256=str(index) * 64, status=DocumentStatus.READY,
            title=f"Paper {index}",
        ) for index in (1, 2)]
        session.add_all(documents)
        session.commit()
        result = await PaperComparisonService(
            session, retriever=CoverageRetriever(), provider=provider,
        ).compare(documents, "Compare exact layer-normalization placement")
    assert len(provider.prompts) == 2
    assert "Paper 2" in provider.prompts[1]
    assert "Recheck the supplied passages once" in provider.prompts[1]
    assert result.insufficient_evidence is remain_partial
    assert result.audit.cross_document_claim_count == (0 if remain_partial else 1)
    assert [step.status for step in result.trace if step.skill == "llm.audit_source_coverage"] == [
        "insufficient" if remain_partial else "ok"
    ]
    assert result.evidence and result.provider == "deepseek"


def test_normalization_query_without_colon_gets_a_focused_retrieval_facet():
    assert _comparison_facets("Compare Transformer and GPT-2 layer normalization placement") == [
        "layer normalization placement residual connection input sub-block"
    ]


def test_unknown_evidence_id_cannot_cover_a_missing_paper():
    papers = [ComparisonPaperRead(
        document_id=doc, title=doc, evidence_count=1, evidence_ids=[eid], coverage="supported",
    ) for doc, eid in [("A", "E1"), ("B", "E2")]]
    evidence = [EvidenceAnchor(
        evidence_id=eid, document_id=doc, page_number=1, quote="Evidence", score=0.9,
    ) for doc, eid in [("A", "E1"), ("B", "E2")]]
    missing = _missing_generated_source_papers(
        papers, [GeneratedClaim(text="Both papers", evidence_ids=["E1", "unknown"])], evidence,
    )
    assert [paper.document_id for paper in missing] == ["B"]


@pytest.mark.parametrize("case_ids", [[], ["missing"], ["a", "a"]])
def test_targeted_evaluation_rejects_invalid_selection(case_ids):
    with pytest.raises(ValueError):
        _select_evaluation_cases({"cases": [{"case_id": "a"}]}, case_ids)


def test_targeted_evaluation_does_not_mutate_the_full_dataset():
    dataset = {"dataset_id": "test", "cases": [{"case_id": "a"}, {"case_id": "b"}]}
    assert _select_evaluation_cases(dataset, None) is dataset
    targeted = _select_evaluation_cases(dataset, ["b"])
    assert targeted["cases"] == [{"case_id": "b"}]
    assert len(dataset["cases"]) == 2


@pytest.mark.parametrize(
    ("text", "gpt2_page", "expected"),
    [
        ("Attention Is All You Need and GPT-2 differ in LayerNorm placement.", 4, True),
        ("Attention Is All You Need and GPT-2 differ in LayerNorm placement.", 3, False),
        ("Attention Is All You Need uses LayerNorm.", 4, False),
    ],
)
def test_canonical_title_alias_keeps_two_source_constraints(text, gpt2_page, expected):
    dataset_path = Path(__file__).parents[1] / "evals/real_pdf_comparison_grounded.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    case = next(
        item for item in dataset["cases"]
        if item["case_id"] == "transformer-vs-gpt2-layernorm-conflict"
    )
    response = SimpleNamespace(
        claims=[GeneratedClaim(text=text, evidence_ids=["E1", "E2"])],
        evidence=[
            EvidenceAnchor(evidence_id="E1", document_id="A", page_number=2,
                           quote="LayerNorm(x + Sublayer(x))", score=0.9),
            EvidenceAnchor(evidence_id="E2", document_id="B", page_number=gpt2_page,
                           quote="Layer normalization was moved to the input of each sub-block",
                           score=0.9),
        ],
    )
    assert _check_cross_document_fact(
        case["required_cross_document_facts"][0], response,
        {"1706.03762v1.pdf": "A", "gpt2-openai-2019.pdf": "B"},
    ) is expected


@pytest.mark.parametrize(
    ("text", "page", "ids", "expected"),
    [
        ("GPT-2 将层归一化移到每个子模块的输入处。", 4, ["E2"], True),
        ("GPT-2 将层归一化移到每个子模块的输入处。", 3, ["E2"], False),
        ("GPT-2 将层归一化移到每个子模块的输入处。", 4, ["E1"], False),
        ("GPT-2 将层归一化放在残差之后。", 4, ["E2"], False),
    ],
)
def test_submodule_wording_still_requires_input_placement_and_cited_page(text, page, ids, expected):
    dataset = json.loads((Path(__file__).parents[1] / "evals/real_pdf_comparison_grounded.json")
                         .read_text(encoding="utf-8"))
    case = next(item for item in dataset["cases"]
                if item["case_id"] == "transformer-vs-gpt2-layernorm-conflict")
    response = SimpleNamespace(
        claims=[GeneratedClaim(text=text, evidence_ids=ids)],
        evidence=[EvidenceAnchor(evidence_id="E2", document_id="B", page_number=page,
                                 quote="moved to the input of each sub-block", score=0.9)],
    )
    assert _check_fact(case["required_claim_facts"][1], response,
                       {"gpt2-openai-2019.pdf": "B"}) is expected


def test_regrade_rejects_changed_case_scope_before_parsing_answers():
    with pytest.raises(ValueError, match="exactly the selected cases"):
        regrade_report(
            {"expected_provider": "deepseek", "cases": [{"case_id": "changed"}]},
            {"provider": "deepseek", "model": "deepseek-flash", "cases": []},
            source_path="original.json", source_sha256="a" * 64,
        )
