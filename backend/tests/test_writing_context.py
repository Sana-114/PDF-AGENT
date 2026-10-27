from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services import writing_context
from app.services.writing_outline import _clean_reference_title


def _block(block_id, text, index, kind="text"):
    return {
        "block_id": block_id,
        "type": kind,
        "text": text,
        "reading_order": index,
        "bbox": [0.0, float(index * 10), 200.0, float(index * 10 + 9)],
    }


def _anchor():
    return EvidenceAnchor(
        evidence_id="E1",
        chunk_id="chunk-one",
        document_id="paper-one",
        page_number=1,
        block_ids=["p1-b1"],
        quote=(
            "An initial classifier comparison performed poorly. "
            "However, we later found a confound. Instead,"
        ),
        score=0.9,
    )


def test_neighbor_in_another_section_keeps_its_own_heading_and_is_not_a_continuation(monkeypatch):
    root = _anchor().model_copy(update={"page_number": 4, "block_ids": ["p4-body"],
        "section": "4 EXPERIMENTS", "quote": "The experiments compare multiple methods "
        "on the published classification benchmarks using source-supported measurements."})
    fine_tuning = "The prediction head is replaced before fine-tuning at higher resolution."
    parsed = {"pages": [{"page_number": 4, "blocks": [
        _block("p4-h3", "3.2 FINE-TUNING", 0, "heading"),
        _block("p4-earlier", fine_tuning, 1),
        _block("p4-h4", "4 EXPERIMENTS", 2, "heading"),
        _block("p4-body", root.quote, 3),
    ]}]}
    monkeypatch.setattr(writing_context, "read_parsed_document", lambda _: parsed)
    expanded = writing_context.expand_writing_evidence([root])
    neighbor = next(item for item in expanded if item.block_ids == ["p4-earlier"])
    assert neighbor.section == "3.2 FINE-TUNING"
    assert neighbor.context_for == [] and neighbor.retrieval_mode == "local_context"
    assert neighbor.score == 0 and neighbor.recall_mode is None
    assert expanded[0] is root
    assert neighbor.quote == fine_tuning


def test_unknown_section_is_not_inherited_from_neighbor_and_heading_carries_over_pages(monkeypatch):
    from app.services.document_content import block_sections

    parsed = {"pages": [
        {"page_number": 1, "blocks": [_block("unknown", "An opening paragraph.", 0),
         _block("h", "2 METHOD", 1, "heading")]},
        {"page_number": 2, "blocks": [_block("body", "Continued method.", 0)]},
    ]}
    assert block_sections(parsed) == {(1, "unknown"): None, (1, "h"): "2 METHOD",
                                     (2, "body"): "2 METHOD"}


def test_abstract_is_a_separate_body_anchor_not_a_relabelled_introduction(monkeypatch):
    overview = "An overview describes this paper's method and scope with enough original text."
    root = _anchor().model_copy(update={"quote": "This introductory paragraph is complete."})
    parsed = {"pages": [{"page_number": 1, "blocks": [
        _block("p1-title", "A misleading title is not source evidence", 0, "title"),
        _block("p1-ah", "ABSTRACT", 1, "heading"),
        _block("p1-abstract", overview, 2),
        _block("p1-ih", "1 Introduction", 3, "heading"),
        _block("p1-b1", root.quote, 4),
    ]}]}
    monkeypatch.setattr(writing_context, "read_parsed_document", lambda _: parsed)
    result = writing_context.expand_writing_evidence([root])
    assert result[0] is root
    abstract = next(e for e in result if e.source_type == "abstract")
    assert abstract.quote == overview and abstract.block_ids == ["p1-abstract"]
    assert abstract.bbox == parsed["pages"][0]["blocks"][2]["bbox"]
    assert abstract.retrieval_mode == "local_context" and abstract.context_for == []
    assert abstract.score == 0 and abstract.recall_mode is None
    assert abstract.document_id == root.document_id


def test_absent_abstract_heading_does_not_fabricate_overview_and_existing_block_not_duplicated(
    monkeypatch,
):
    overview = "The method description here has sufficient original prose to qualify as a block."
    parsed = {"pages": [{"page_number": 1, "blocks": [
        _block("p1-ah", "Abstract", 0, "heading"),
        _block("p1-b1", overview, 1),
    ]}]}
    root = _anchor().model_copy(update={"quote": overview})
    monkeypatch.setattr(writing_context, "read_parsed_document", lambda _: parsed)
    assert writing_context.expand_writing_evidence([root]) == [root]
    parsed["pages"][0]["blocks"][0]["text"] = "Introduction"
    assert writing_context._abstract_blocks(parsed) == []


def test_required_continuation_keeps_priority_over_abstract_under_one_block_budget(
    monkeypatch,
):
    root = _anchor()
    parsed = {"pages": [
        {"page_number": 1, "blocks": [
            _block("p1-ah", "Abstract", 0, "heading"),
            _block("p1-ab", "A separate abstract method overview with enough original content.", 1),
            _block("p1-ih", "Introduction", 2, "heading"),
            _block("p1-b1", root.quote, 3),
        ]},
        {"page_number": 2, "blocks": [
            _block("p2-b1", "However, the original result only holds after tuning "
                   "the learning rate.", 0),
        ]},
    ]}
    monkeypatch.setattr(writing_context, "read_parsed_document", lambda _: parsed)
    monkeypatch.setattr(writing_context, "MAX_CONTEXT_BLOCKS", 1)
    result = writing_context.expand_writing_evidence([root])
    assert len(result) == 2
    assert result[1].page_number == 2 and result[1].context_for == [root.evidence_id]


def test_cross_page_context_has_its_own_source_anchor_and_keeps_original(monkeypatch):
    parsed = {
        "pages": [
            {
                "page_number": 1,
                "blocks": [
                    _block("p1-b1", _anchor().quote, 0),
                ],
            },
            {
                "page_number": 2,
                "blocks": [
                    _block(
                        "p2-b1",
                        "A Real Scientific Article with a Repeated Running Header on Every Page 2",
                        0,
                    ),
                    _block(
                        "p2-b2",
                        "Figure 9: Both classifiers work similarly well "
                        "after the learning rate is tuned.",
                        1,
                        "figure_caption",
                    ),
                    _block(
                        "p2-b3",
                        "The difference in performance is fully explained by "
                        "a different learning rate, see Figure 9.",
                        2,
                    ),
                    _block("p2-b4", "A NEW SECTION", 3, "heading"),
                    _block(
                        "p2-b5",
                        "An unrelated later discussion with enough text "
                        "to appear as prose in a different section.",
                        4,
                    ),
                ],
            },
        ],
        "references": [],
    }
    monkeypatch.setattr(writing_context, "read_parsed_document", lambda _: parsed)
    anchor = _anchor()
    expanded = writing_context.expand_writing_evidence([anchor])
    assert expanded[0] is anchor
    assert [item.block_ids for item in expanded[1:]] == [["p2-b2"], ["p2-b3"]]
    assert [item.page_number for item in expanded[1:]] == [2, 2]
    assert [item.evidence_id for item in expanded] == ["E1", "E2", "E3"]
    assert all(item.context_for == ["E1"] for item in expanded[1:])
    assert expanded[1].source_type == "figure"
    assert expanded[2].bbox == [0.0, 20.0, 200.0, 29.0]


def test_reference_evidence_cannot_expand_into_unrelated_paper_text(monkeypatch):
    parsed = {
        "pages": [
            {
                "page_number": 1,
                "blocks": [
                    _block(
                        "p1-b1",
                        "A long bibliography entry that has no explanatory "
                        "relation to the writing idea.",
                        0,
                    ),
                ],
            }
        ],
        "references": [{"block_ids": ["p1-b1"]}],
    }
    monkeypatch.setattr(writing_context, "read_parsed_document", lambda _: parsed)
    anchor = _anchor().model_copy(update={"quote": parsed["pages"][0]["blocks"][0]["text"]})
    assert writing_context.expand_writing_evidence([anchor]) == [anchor]


def test_reference_display_joins_line_wrapped_word_but_preserves_compounds():
    assert _clean_reference_title("LORA: LOW-RANK ADAPTATION OF LARGE LAN- GUAGE MODELS") == (
        "LORA: LOW-RANK ADAPTATION OF LARGE LANGUAGE MODELS"
    )
    assert _clean_reference_title("A LOW- RANK METHOD") == "A LOW-RANK METHOD"


def test_unfinished_page_claim_needs_its_continuation_citation():
    root = _anchor()
    context = root.model_copy(
        update={
            "evidence_id": "E2",
            "page_number": 2,
            "block_ids": ["p2-b2"],
            "quote": "Both classifiers perform similarly after tuning the learning rate.",
            "context_for": ["E1"],
        }
    )
    claims = [
        AnswerClaim(text="The first comparison performed poorly.", evidence_ids=["E1"]),
        AnswerClaim(
            text=(
                "The first comparison performed poorly, but the tuned "
                "classifiers perform similarly."
            ),
            evidence_ids=["E1", "E2"],
        ),
    ]
    published, withheld = writing_context.withhold_unqualified_continuation_claims(
        claims, [root, context]
    )
    assert withheld == 1
    assert published == claims[1:]


def _sectioned_evidence():
    root = _anchor().model_copy(
        update={
            "quote": (
                "2.1 DEPTH SCALING\n"
                "Increasing encoder depth improves recognition accuracy across "
                "several benchmark datasets. Width changes provide smaller "
                "gains under the same computation budget.\n"
                "2.2 CLASSIFIER COMPARISON\n"
                "An initial pooling classifier experiment performed poorly. "
                "However, this performance difference had a confound. Instead,"
            )
        }
    )
    continuation = root.model_copy(
        update={
            "evidence_id": "E2",
            "page_number": 2,
            "quote": "Learning rate tuning explains the gap between the two classifiers.",
            "context_for": ["E1"],
        }
    )
    return root, continuation


def test_complete_other_section_is_not_held_by_unrelated_next_page_condition():
    evidence = _sectioned_evidence()
    finished_fact = AnswerClaim(
        text="Increasing encoder depth improves recognition accuracy across benchmark datasets.",
        evidence_ids=["E1"],
    )
    incomplete_result = AnswerClaim(
        text="The pooling classifier performed poorly in the experiment.", evidence_ids=["E1"]
    )
    mixed_result = AnswerClaim(
        text=(
            "Increasing encoder depth improves recognition accuracy across benchmark datasets, "
            "and the pooling classifier experiment performed poorly."
        ),
        evidence_ids=["E1"],
    )
    claims, count = writing_context.withhold_unqualified_continuation_claims(
        [finished_fact, incomplete_result, mixed_result], evidence
    )
    assert claims == [finished_fact]
    assert count == 2


def test_uncertain_cross_language_claim_still_requires_continuation():
    claim = AnswerClaim(text="池化分类器实验表现较差。", evidence_ids=["E1"])
    published, count = writing_context.withhold_unqualified_continuation_claims(
        [claim], _sectioned_evidence()
    )
    assert not published
    assert count == 1


def test_same_page_or_other_paper_citation_cannot_satisfy_continuation():
    root, continuation = _sectioned_evidence()
    wrong_paper = continuation.model_copy(
        update={"evidence_id": "E3", "document_id": "another-paper"}
    )
    same_page = continuation.model_copy(update={"evidence_id": "E4", "page_number": 1})
    claim = AnswerClaim(
        text="The pooling classifier performed poorly.", evidence_ids=["E1", "E3", "E4"]
    )
    published, count = writing_context.withhold_unqualified_continuation_claims(
        [claim], [root, continuation, wrong_paper, same_page]
    )
    assert not published
    assert count == 1


def test_line_break_before_however_does_not_release_incomplete_experimental_result():
    root, continuation = _sectioned_evidence()
    root = root.model_copy(
        update={
            "quote": (
                "An initial pooling classifier experiment performed very poorly "
                "in our recognition benchmark and training configuration.\n"
                "However, we later found that the difference was neither due to "
                "the representation nor to the pooling operation. Instead,"
            )
        }
    )
    claim = AnswerClaim(
        text=(
            "The pooling classifier experiment performed very poorly "
            "in the recognition benchmark and training configuration."
        ),
        evidence_ids=["E1"],
    )
    published, count = writing_context.withhold_unqualified_continuation_claims(
        [claim], [root, continuation]
    )
    assert not published
    assert count == 1


def test_already_ranked_next_page_block_retains_its_anchor_and_links_all_roots(monkeypatch):
    root = _anchor()
    next_text = (
        "Learning rate tuning fully explains the observed classifier performance "
        "difference, and both configurations achieve similar accuracy."
    )
    parsed = {
        "pages": [
            {"page_number": 1, "blocks": [_block("p1-b1", root.quote, 0)]},
            {"page_number": 2, "blocks": [_block("p2-b1", next_text, 0)]},
        ],
        "references": [],
    }
    monkeypatch.setattr(writing_context, "read_parsed_document", lambda _: parsed)
    duplicate_root = root.model_copy(update={"evidence_id": "E2"})
    ranked_next = root.model_copy(
        update={
            "evidence_id": "E3",
            "page_number": 2,
            "block_ids": ["p2-b1"],
            "quote": next_text,
            "score": 0.8,
        }
    )
    expanded = writing_context.expand_writing_evidence([root, duplicate_root, ranked_next])
    assert len(expanded) == 3
    assert expanded[2].context_for == ["E1", "E2"]
    assert expanded[2].model_dump(exclude={"context_for"}) == ranked_next.model_dump(
        exclude={"context_for"}
    )
    assert ranked_next.context_for == []
    bad = AnswerClaim(text="The first classifier performed poorly.", evidence_ids=["E1"])
    assert writing_context.withhold_unqualified_continuation_claims([bad], expanded) == ([], 1)
