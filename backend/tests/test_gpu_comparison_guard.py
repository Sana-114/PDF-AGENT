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
from app.services.paper_comparison import (
    PaperComparisonService,
    _comparison_facets,
    _gpu_hardware_values,
    _requested_gpu_fields,
)
from scripts.evaluate_real_pdf_comparison import _check_fact


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Compare exact GPU model and GPU count for pre-training", {"model", "count"}),
        ("比较训练 GPU 的型号和数量", {"model", "count"}),
        ("Compare the number of GPUs", {"count"}),
        ("Compare GPU model", {"model"}),
        ("Compare Transformer parameter counts", set()),
    ],
)
def test_gpu_request_fields(question, expected):
    assert _requested_gpu_fields(question) == expected
    if expected:
        assert "training hardware GPU model number of GPUs" in _comparison_facets(question)


@pytest.mark.parametrize(
    ("quote", "models", "counts"),
    [
        ("We trained on 8 NVIDIA V100 GPUs.", {"v100"}, {"8"}),
        ("We trained on sixteen Tesla P100 GPUs.", {"p100"}, {"sixteen"}),
        ("训练使用 8 张 NVIDIA V100 GPU。", {"v100"}, {"8"}),
        ("We trained on 64 TPU v3 chips.", set(), set()),
        ("The training model has 12 layers and 8 attention heads.", set(), set()),
        ("We did not train on 8 V100 GPUs.", set(), set()),
        ("Training used 12 layers and 8 NVIDIA V100 GPUs.", {"v100"}, {"8"}),
        ("Inference uses 8 NVIDIA V100 GPUs.", set(), set()),
    ],
)
def test_only_explicit_training_gpu_values_are_accepted(quote, models, counts):
    assert _gpu_hardware_values(quote, training_only=True) == {"model": models, "count": counts}


class HardwareRetriever:
    def __init__(self, quotes: dict[str, str], *, section="Training"):
        self.quotes = quotes
        self.section = section

    def search(self, question, document_ids=None, top_k=6):
        return [EvidenceAnchor(
            evidence_id="local", document_id=document_ids[0], page_number=2,
            block_ids=[f"{document_ids[0]}-hardware"], bbox=[10, 20, 300, 80],
            quote=self.quotes[document_ids[0]], section=self.section,
            score=0.95, retrieval_mode="reranked",
        )]


class HardwareProvider:
    name = "deepseek"
    model = "deepseek-flash"
    supports_translation = True

    def __init__(self, *, absent=False, generic=False):
        self.absent = absent
        self.generic = generic

    async def generate_grounded_answer(self, question, evidence):
        if self.absent:
            return GeneratedAnswer(answer="No hardware details", claims=[
                GeneratedClaim(
                    text=f"[SINGLE_SOURCE][DIMENSION: hardware] Paper {label}'s selected "
                         "passages contain no GPU model or GPU count.",
                    evidence_ids=[anchor.evidence_id],
                ) for label, anchor in zip(("A", "B"), evidence, strict=True)
            ])
        text = "Both papers describe their architectures." if self.generic else (
            "Paper A uses 8 NVIDIA V100 GPUs; Paper B uses 16 NVIDIA A100 GPUs."
        )
        return GeneratedAnswer(answer="grounded", claims=[GeneratedClaim(
            text=f"[DIFFERENCE][DIMENSION: hardware] {text}",
            evidence_ids=[item.evidence_id for item in evidence],
        )])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["absence", "tpu", "one-sided", "generic", "related-work", "supported"],
)
async def test_gpu_comparison_requires_requested_fields_from_both_sources(scenario):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        documents = [Document(
            original_filename=f"paper-{index}.pdf", storage_key=f"paper-{index}.pdf",
            size_bytes=100, sha256=str(index) * 64, status=DocumentStatus.READY,
            title=f"Paper {index}",
        ) for index in (1, 2)]
        session.add_all(documents)
        session.commit()
        quotes = ["We trained on 8 NVIDIA V100 GPUs.", "We trained on 16 NVIDIA A100 GPUs."]
        if scenario in {"absence", "tpu"}:
            quotes = ["We trained on 16 TPU chips.", "We trained a decoder with 12 layers."]
        elif scenario == "one-sided":
            quotes[1] = "We trained on TPU chips."
        result = await PaperComparisonService(
            session,
            retriever=HardwareRetriever(
                {doc.id: quote for doc, quote in zip(documents, quotes, strict=True)},
                section="Related Work" if scenario == "related-work" else "Training",
            ),
            provider=HardwareProvider(absent=scenario == "absence", generic=scenario == "generic"),
        ).compare(documents, "Compare exact GPU model and GPU count used for pre-training.")
    if scenario == "supported":
        assert not result.insufficient_evidence
        assert result.audit.cross_document_claim_count == 1
        assert "V100" in result.answer and "A100" in result.answer
    else:
        assert result.insufficient_evidence
        assert not result.claims
        assert result.audit.derived_claim_count == 0
        assert result.audit.cross_document_claim_count == 0
        assert "requested_gpu_hardware_not_supported" in result.audit.rejection_reasons
        assert "不能完成" in result.answer


@pytest.mark.parametrize(
    ("text", "page", "expected"),
    [
        ("GPT-1 trains on sequences of 512 tokens.", 5, True),
        ("GPT-1 trains on sequences of 512 tokens.", 3, False),
        ("GPT-3 trains on sequences of 512 tokens.", 5, False),
        ("GPT-1 has a context window of 512 tokens.", 5, False),
    ],
)
def test_training_verb_gold_fix_keeps_identity_page_and_training_constraints(text, page, expected):
    dataset_path = Path(__file__).parents[1] / "evals/real_pdf_comparison_grounded.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    case = next(item for item in dataset["cases"] if item["case_id"] == "gpt-1-2-3-evolution")
    fact = case["required_claim_facts"][0]
    response = SimpleNamespace(
        claims=[GeneratedClaim(text=text, evidence_ids=["E1"])],
        evidence=[EvidenceAnchor(
            evidence_id="E1", document_id="A", page_number=page,
            quote="We train on sequences of 512 tokens.", score=0.9,
        )],
    )
    assert _check_fact(fact, response, {"gpt1-openai-2018.pdf": "A"}) is expected
