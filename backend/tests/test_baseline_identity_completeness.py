from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.answer_completeness import context_comparison_guidance, missing_fields
from app.services.baseline_evidence import baseline_reference_bridges


def sources():
    return [
        EvidenceAnchor(
            evidence_id="E1",
            document_id="early",
            document_title="Earlier language model architecture",
            page_number=1,
            quote="The original model.",
            score=1,
        ),
        EvidenceAnchor(
            evidence_id="E2",
            document_id="later",
            document_title="Later language model architecture",
            page_number=4,
            source_type="text",
            score=1,
            quote="We follow Model-Z (AuthorX et al., 2022). "
            "We increase the context size from 256 to 512 tokens.",
        ),
        EvidenceAnchor(
            evidence_id="E5",
            document_id="later",
            document_title="Later language model architecture",
            page_number=9,
            source_type="reference",
            score=1,
            quote="AuthorX. Earlier language model architecture. 2022.",
        ),
    ]


def test_source_title_bridge_returns_ids_but_does_not_fill_a_model_or_quantity():
    evidence = sources()
    assert baseline_reference_bridges([evidence[1]], evidence) == [{"E5"}]
    hint = context_comparison_guidance("GPT-1 和 GPT-2 上下文窗口分别多少？", evidence)
    assert "reference entries" in hint and "E5" in hint
    assert "256" not in hint and "512" not in hint


def test_available_uncited_identity_bridge_keeps_earlier_field_incomplete():
    evidence = sources()
    claims = [
        AnswerClaim(
            text="GPT-1 的上下文窗口为 256 tokens（推断；来源说明：后出论文基线比较）。",
            evidence_ids=["E2"],
        ),
        AnswerClaim(text="GPT-2 的上下文窗口为 512 tokens。", evidence_ids=["E2"]),
    ]
    assert missing_fields("GPT-1 和 GPT-2 上下文窗口分别多少？", claims, evidence) == [
        "GPT-1 最大上下文及来源说明"
    ]
    claims[0].evidence_ids.append("E5")
    assert not missing_fields("GPT-1 和 GPT-2 上下文窗口分别多少？", claims, evidence)


def test_wrong_year_nonreference_wrong_document_and_unmatched_title_are_not_proof():
    for updates in [
        {"quote": "AuthorX. Earlier language model architecture. 2021."},
        {"source_type": "text"},
        {"document_id": "unrelated"},
        {"quote": "AuthorX. Some other paper. 2022."},
    ]:
        evidence = sources()
        evidence[2] = evidence[2].model_copy(update=updates)
        assert not baseline_reference_bridges([evidence[1]], evidence)


def test_two_selected_documents_with_same_title_are_ambiguous():
    evidence = sources()
    evidence.append(
        evidence[0].model_copy(update={"document_id": "ambiguous", "evidence_id": "E6"})
    )
    assert not baseline_reference_bridges([evidence[1]], evidence)


def test_identity_citation_without_numeric_comparison_does_not_fill_missing_value():
    evidence = sources()
    claim = AnswerClaim(text="GPT-1 的上下文窗口为 256 tokens（推断）。", evidence_ids=["E5"])
    assert missing_fields("GPT-1 上下文窗口多少？", [claim], evidence) == [
        "GPT-1 最大上下文及来源说明"
    ]
