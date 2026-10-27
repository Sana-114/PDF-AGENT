import copy
from datetime import UTC, datetime

import pytest

from app.schemas.citation_graph import (
    CitationGraphEdgeRead,
    CitationGraphNodeRead,
    CitationGraphRead,
    CitationGraphStatsRead,
)
from app.schemas.review import ReviewEvidenceRead
from app.services.review_context import anchored_citation_relations, expand_review_context


def node(key, title):
    return CitationGraphNodeRead(
        document_id=key,
        title=title,
        authors=[],
        arxiv_id="2401.01234" if key == "target" else None,
        arxiv_version=None,
        page_count=8,
        in_degree=1,
        out_degree=1,
        pagerank=0.5,
        foundation_score=0.5,
        role="bridge",
    )


def fixtures():
    quote = "A. Author. Original local attention study. 2024."
    graph = CitationGraphRead(
        generated_at=datetime.now(UTC),
        nodes=[
            node("source", "A source architecture paper"),
            node("target", "Original local attention study"),
        ],
        edges=[
            CitationGraphEdgeRead(
                edge_id="source:target",
                source_document_id="source",
                target_document_id="target",
                reference_ids=["ref-one"],
                reference_labels=["1"],
                reference_pages=[6],
                sample_reference=quote,
                mention_count=1,
                match_score=0.98,
                match_reason="title",
            )
        ],
        stats=CitationGraphStatsRead(
            document_count=2,
            relation_count=1,
            total_references=1,
            matched_references=1,
            unmatched_references=0,
            cornerstone_count=0,
            derivative_count=0,
            density=0.5,
        ),
        warnings=[],
    )
    parsed = {
        "source": {
            "references": [
                {"reference_id": "ref-one", "text": quote, "page_number": 6, "block_ids": ["ref-b"]}
            ],
            "pages": [
                {
                    "page_number": 6,
                    "blocks": [{"block_id": "ref-b", "text": quote, "bbox": [10, 20, 350, 40]}],
                }
            ],
        }
    }
    return graph, parsed


def test_relation_preserves_exact_page_local_quote_and_does_not_modify_input():
    graph, parsed = fixtures()
    original = copy.deepcopy(parsed)
    evidence, relations = anchored_citation_relations(graph, parsed)
    assert parsed == original
    assert len(evidence) == len(relations) == 1
    assert evidence[0].evidence_id == relations[0].evidence_id == "G1"
    assert evidence[0].quote == parsed["source"]["references"][0]["text"]
    assert evidence[0].block_ids == ["ref-b"] and evidence[0].page_number == 6
    assert (
        evidence[0].source_type == "reference" and evidence[0].evidence_kind == "citation_relation"
    )
    assert relations[0].source_document_id == "source"
    assert relations[0].target_document_id == "target"


@pytest.mark.parametrize(
    "damage",
    [
        "fuzzy",
        "missing_block",
        "cross_page",
        "no_bbox",
        "overlong",
        "wrong_ref_id",
        "wrong_quote",
        "arxiv_prefix",
    ],
)
def test_unanchored_or_uncertain_reference_is_not_used_for_generation(damage):
    graph, parsed = fixtures()
    ref = parsed["source"]["references"][0]
    block = parsed["source"]["pages"][0]["blocks"][0]
    if damage == "fuzzy":
        ref["text"] = block["text"] = "A. Author. A different attention study. 2024."
    elif damage == "missing_block":
        ref["block_ids"] = []
    elif damage == "cross_page":
        ref["block_ids"].append("next-page")
        parsed["source"]["pages"].append(
            {
                "page_number": 7,
                "blocks": [
                    {"block_id": "next-page", "text": "Continued reference", "bbox": [1, 1, 2, 2]}
                ],
            }
        )
    elif damage == "no_bbox":
        block["bbox"] = None
    elif damage == "overlong":
        ref["text"] = block["text"] = ref["text"] + "x" * 1600
    elif damage == "wrong_ref_id":
        ref["reference_id"] = "other"
    elif damage == "wrong_quote":
        block["text"] = "Unrelated original block."
    else:
        ref["text"] = block["text"] = "Author. arXiv:2401.012345."
    assert anchored_citation_relations(graph, parsed) == ([], [])


def test_exact_arxiv_identity_is_allowed_without_matching_title():
    graph, parsed = fixtures()
    parsed["source"]["references"][0]["text"] = "Author. arXiv:2401.01234v2."
    parsed["source"]["pages"][0]["blocks"][0]["text"] = "Author. arXiv:2401.01234v2."
    assert len(anchored_citation_relations(graph, parsed)[0]) == 1


def test_duplicate_selected_titles_are_ambiguous_and_limit_zero_sends_no_graph_text():
    graph, parsed = fixtures()
    assert anchored_citation_relations(graph, parsed, limit=0) == ([], [])
    graph.nodes.append(node("another-version", graph.nodes[1].title))
    assert anchored_citation_relations(graph, parsed) == ([], [])


def test_graph_inline_ids_are_checked_like_other_citations():
    from app.llm.base import GeneratedClaim
    from app.services.grounded_text import checked_claims

    graph, parsed = fixtures()
    evidence, _ = anchored_citation_relations(graph, parsed)
    good = GeneratedClaim(
        text="The bibliography lists the local attention study. [G1]", evidence_ids=["G1"]
    )
    bad = GeneratedClaim(
        text="The bibliography lists the local attention study. [G2]", evidence_ids=["G1"]
    )
    claims, rejected = checked_claims([good, bad], evidence)
    assert len(claims) == rejected == 1 and "[G1]" not in claims[0].text


def diverse_fixtures():
    graph, parsed = fixtures()
    # A second source cites the same central work; a third cites another work.
    for source, target, title in [
        ("second", "target", "Original local attention study"),
        ("third", "alternative", "A distinct memory optimization study"),
    ]:
        graph.nodes.append(node(source, f"Architecture source {source}"))
        if target == "alternative":
            graph.nodes.append(node(target, title).model_copy(update={"foundation_score": 0.1}))
        quote = f"Author. {title}. 2024."
        graph.edges.append(graph.edges[0].model_copy(update={
            "edge_id": source + ":" + target,
            "source_document_id": source,
            "target_document_id": target,
        }))
        parsed[source] = copy.deepcopy(parsed["source"])
        parsed[source]["references"][0]["text"] = quote
        parsed[source]["pages"][0]["blocks"][0]["text"] = quote
    return graph, parsed


def test_tiny_budget_covers_distinct_endpoints_not_only_a_central_target():
    graph, parsed = diverse_fixtures()
    original = copy.deepcopy(parsed)
    evidence, relations = anchored_citation_relations(graph, parsed, limit=2)
    assert len(evidence) == len(relations) == 2
    assert {r.target_document_id for r in relations} == {"target", "alternative"}
    assert len({r.source_document_id for r in relations}) == 2
    assert parsed == original


def test_diversity_does_not_bypass_an_invalid_original_reference():
    graph, parsed = diverse_fixtures()
    parsed["third"]["references"][0]["text"] = "A vague memory-related reference."
    evidence, relations = anchored_citation_relations(graph, parsed, limit=2)
    assert len(evidence) == len(relations) == 2
    assert {r.target_document_id for r in relations} == {"target"}
    assert all(r.source_document_id != "third" for r in relations)


def test_endpoint_diversity_remains_deterministic_under_edge_order_changes():
    graph, parsed = diverse_fixtures()
    expected = anchored_citation_relations(graph, parsed, limit=2)
    graph.edges.reverse()
    assert anchored_citation_relations(graph, parsed, limit=2) == expected


def test_single_relation_retains_the_strong_identity_centrality_tiebreak():
    graph, parsed = diverse_fixtures()
    _, relations = anchored_citation_relations(graph, parsed, limit=1)
    assert len(relations) == 1 and relations[0].target_document_id == "target"


def context_fixture():
    root = "We leave fine-tuning experiments to future work. " * 2
    tail = "These learning curves involve no gradient updates; they add demonstrations only."
    item = ReviewEvidenceRead(evidence_id="R1", document_id="source", page_number=5,
        block_ids=["root"], bbox=[20, 100, 500, 180], quote=root.strip(), score=1,
        evidence_kind="future_work")
    parsed = {"source": {"pages": [{"page_number": 5, "blocks": [
        {"block_id": "root", "type": "text", "text": root, "bbox": item.bbox},
        {"block_id": "tail", "type": "text", "text": tail, "bbox": [20, 190, 500, 250]},
    ]}]}}
    return item, parsed, tail


def test_review_neighbor_is_literal_same_page_and_keeps_originals_unmodified():
    item, parsed, tail = context_fixture()
    original, untouched = item.model_dump(), copy.deepcopy(parsed)
    expanded, count = expand_review_context([item], parsed)
    assert count == len(tail) + 1 and expanded[0].quote == item.quote + " " + tail
    assert expanded[0].block_ids == ["root", "tail"]
    assert expanded[0].bbox == [20, 100, 500, 250]
    assert item.model_dump() == original and parsed == untouched


@pytest.mark.parametrize("defect", ["heading", "reference", "footnote", "caption", "truncated",
                                   "bad_bbox", "budget", "private", "next_page"])
def test_context_does_not_skip_scope_boundaries_or_synthesize_missing_text(defect):
    item, parsed, tail = context_fixture()
    blocks = parsed["source"]["pages"][0]["blocks"]
    kwargs = {}
    if defect in {"heading", "footnote"}:
        blocks[1]["type"] = defect
    elif defect == "reference":
        parsed["source"]["references"] = [{"block_ids": ["tail"]}]
    elif defect == "caption":
        blocks[1].update(type="figure_caption", text="Figure 2: " + tail)
    elif defect == "truncated":
        item.quote = item.quote[:50]
    elif defect == "bad_bbox":
        blocks[1]["bbox"] = [0, float("nan"), 20, 80]
    elif defect == "budget":
        kwargs["max_added_chars"] = 10
    elif defect == "private":
        parsed["private"] = parsed.pop("source")
    elif defect == "next_page":
        neighbor = blocks.pop()
        parsed["source"]["pages"].append({"page_number": 6, "blocks": [neighbor]})
    expanded, count = expand_review_context([item], parsed, **kwargs)
    assert expanded == [item] and count == 0


def test_figure_referencing_narrative_is_not_confused_with_actual_caption_label():
    item, parsed, tail = context_fixture()
    parsed["source"]["pages"][0]["blocks"][1].update(
        type="figure_caption", text="Figure 2 illustrates the conditions. " + tail
    )
    expanded, count = expand_review_context([item], parsed)
    assert count > 0 and tail in expanded[0].quote
