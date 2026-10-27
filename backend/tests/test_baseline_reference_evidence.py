import pytest

from app.schemas.agent import EvidenceAnchor
from app.services.answer_completeness import affirmative_model_values, contract_prompt
from app.services.baseline_evidence import baseline_citations, matches_baseline_reference
from app.services.claim_semantics import claim_semantic_conflicts
from app.services.retrieval import HybridRetriever


def anchor(quote, key="E1", source="text", score=1):
    return EvidenceAnchor(evidence_id=key, chunk_id=key, document_id="doc", page_number=4,
                          quote=quote, source_type=source, score=score)


BODY = ("The model largely follows details of the Model-Z (AuthorX et al., 2022) "
        "with a few modifications. We increase the context size from 256 to 512 tokens.")


def test_citation_is_read_from_a_real_context_comparison_not_fixed_model_aliases():
    assert baseline_citations([anchor(BODY)]) == [("doc", "AuthorX", "2022")]
    assert baseline_citations([anchor(BODY), anchor(BODY, "E2")]) == [
        ("doc", "AuthorX", "2022")]


@pytest.mark.parametrize("quote,source", [
    ("We follow Model-Z (AuthorX et al., 2022).", "text"),
    (BODY, "reference"), (BODY, "abstract"),
    ("We use a training sequence length of 512. (AuthorX et al., 2022).", "text"),
])
def test_noncomparison_or_nonbody_passages_do_not_invent_a_baseline(quote, source):
    assert not baseline_citations([anchor(quote, source=source)])


def test_reference_matching_checks_both_author_year_and_source_kind():
    assert matches_baseline_reference(anchor("AuthorX et al. Real paper title. 2022.",
        source="reference"), "AuthorX", "2022")
    assert not matches_baseline_reference(anchor("AuthorX. Wrong paper. 2021.",
        source="reference"), "AuthorX", "2022")
    assert not matches_baseline_reference(anchor("AuthorX. Real paper. 2022."), "AuthorX", "2022")


def test_scored_reference_identity_slot_is_bounded_and_keeps_main_statement():
    class Vector:
        enabled = True

    class Reranker:
        enabled, candidate_k = True, 20
        received_query = None

        def rerank(self, question, evidence, top_k):
            self.received_query = question
            return [item.model_copy(update={"retrieval_mode": "reranked"}) for item in evidence]

    reranker = Reranker()
    retriever = HybridRetriever(None, vector_index=Vector(), reranker=reranker)
    body = anchor(BODY)
    ref = anchor("AuthorX. Model-Z paper. 2022.", "E2", "reference", 0.3)
    low = anchor("AuthorX. Wrong low score. 2022.", "E3", "reference", 0.01)
    selected = retriever._rerank("最大上下文是多少？", [body, low, ref], 2, "hybrid",
                                baseline_keys=["E2", "E3"], baseline_queries=["AuthorX 2022"])
    assert [item.chunk_id for item in selected] == ["E1", "E2"]
    assert selected[1].score == 0.3 and selected[1].quote == ref.quote
    assert all(item.recall_mode == "hybrid" and item.retrieval_mode == "reranked"
               for item in selected)
    assert "AuthorX 2022" in reranker.received_query
    assert "256" not in reranker.received_query


def test_context_scale_synonym_does_not_reinterpret_the_old_value_as_new():
    assert affirmative_model_values("GPT-2：其上下文规模从 512 增加到 1024 tokens。",
                                    "model_context") == {"GPT-2": {"1024"}}


def test_labelled_context_baseline_inference_survives_source_provenance_limitation():
    text = ("GPT-1：证据未直接给出 GPT-1 原论文的最大上下文窗口。根据 GPT-2 论文的配置比较，"
            "可推断其被比较的前代 OpenAI GPT 基线为 512 tokens；不是原论文直接报告的数值。")
    assert "512" in affirmative_model_values(text, "model_context")["GPT-1"]


@pytest.mark.parametrize("text", [
    "GPT-1：上下文未知。GPT-2 从 512 到 1024 tokens。",
    "GPT-1：不能推断上下文基线为 512 tokens。",
    "GPT-1：上下文未知，可推断 GPT-2 的基线为 512 tokens。",
    "GPT-1：训练基线可推断为 512 tokens。",
])
def test_inference_rule_does_not_borrow_refusals_other_models_or_training_only_values(text):
    assert "512" not in affirmative_model_values(text, "model_context").get("GPT-1", set())


def test_inheritance_contract_requires_same_component_and_comparator():
    assert "Compare the SAME parent component" in contract_prompt("比较模型的承袭与丢弃关系")


def test_changed_attention_cannot_borrow_another_explicit_comparator():
    source = anchor("Child-X uses bidirectional self-attention, while the Parent-Y Transformer "
                    "uses constrained self-attention where tokens attend only to the left.")
    assert claim_semantic_conflicts("Child-X 将其自注意力改为双向。", [source]) == [
        "attention_direction_change_uses_different_source_baseline"]
    assert not claim_semantic_conflicts("相对 Parent-Y，Child-X 将其自注意力改为双向。", [source])
    assert not claim_semantic_conflicts("Child-X 使用双向自注意力。", [source])
