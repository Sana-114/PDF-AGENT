"""Give review generation bounded, page-local bibliography relationship proofs.

Graph matches are local bibliography membership, not proof of scientific
inheritance. Reject fuzzy/ambiguous identity and cross-page or missing anchors.
"""

import math
import re

from app.schemas.citation_graph import CitationGraphRead
from app.schemas.review import ReviewCitationRelationRead, ReviewEvidenceRead
from app.services.citation_graph import ARXIV_ID, normalize_title


def expand_review_context(
    evidence: list[ReviewEvidenceRead], parsed: dict[str, dict], *,
    max_quote_chars: int = 2400, max_added_chars: int = 8000,
) -> tuple[list[ReviewEvidenceRead], int]:
    """Include one complete same-page neighbor in bounded review-only excerpts.

    Original AST text and block IDs are the source. No page/section jump, missing
    tail, bibliography, footnote or private document is borrowed. This does not
    change the separately maintained Future Work pool or retrieval scores.
    """
    result, added = [], 0
    for item in evidence:
        if item.evidence_kind != "future_work" or len(item.block_ids) != 1:
            result.append(item)
            continue
        document = parsed.get(item.document_id, {})
        page = next((p for p in document.get("pages", [])
                     if p.get("page_number") == item.page_number), {})
        blocks = page.get("blocks", [])
        position = next((i for i, b in enumerate(blocks)
                         if b.get("block_id") == item.block_ids[0]), None)
        if position is None or position + 1 >= len(blocks):
            result.append(item)
            continue
        root, neighbor = blocks[position], blocks[position + 1]
        text = " ".join(str(neighbor.get("text") or "").split())
        reference_ids = {bid for ref in document.get("references", [])
                         for bid in ref.get("block_ids", [])}
        # A figure-caption-typed narrative paragraph is still original text.
        # Do not skip over actual captions/headings looking for a desired answer.
        allowed = neighbor.get("type") in {"text", "figure_caption"}
        caption_label = re.match(r"(?:fig(?:ure)?\.?|table)\s*\d+(?:\.\d+)*\s*[:.]\s", text, re.I)
        quote = item.quote + " " + text
        boxes = [item.bbox, neighbor.get("bbox")]
        boxes_valid = all(isinstance(b, list) and len(b) == 4
                          and all(isinstance(v, int | float) and math.isfinite(v) for v in b)
                          and b[0] < b[2] and b[1] < b[3] for b in boxes)
        if (not allowed or caption_label or len(text) < 65 or not neighbor.get("block_id")
                or neighbor.get("block_id") in reference_ids
                or " ".join(str(root.get("text") or "").split()) != " ".join(item.quote.split())
                or len(quote) > max_quote_chars or added + len(text) + 1 > max_added_chars
                or not boxes_valid):
            result.append(item)
            continue
        added += len(text) + 1
        result.append(item.model_copy(update={
            "quote": quote, "block_ids": [*item.block_ids, neighbor["block_id"]],
            "bbox": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                     max(b[2] for b in boxes), max(b[3] for b in boxes)],
        }))
    return result, added


def _compact(value: str) -> str:
    return normalize_title(value).replace(" ", "")


def anchored_citation_relations(
    graph: CitationGraphRead, parsed: dict[str, dict], *, limit: int = 4
) -> tuple[list[ReviewEvidenceRead], list[ReviewCitationRelationRead]]:
    nodes = {node.document_id: node for node in graph.nodes}
    evidence, relations, used_sources, used_targets = [], [], set(), set()
    # Strong identities and central targets break ties. A tiny generation
    # budget must also cover distinct endpoints, not four citations to one hub.
    pending = sorted(
        graph.edges,
        key=lambda edge: (
            -edge.match_score,
            -nodes[edge.target_document_id].foundation_score,
            edge.edge_id,
        ),
    )
    while pending and len(relations) < limit:
        covered = used_sources | used_targets
        position = min(
            range(len(pending)),
            key=lambda i: (
                -len({pending[i].source_document_id, pending[i].target_document_id} - covered),
                pending[i].source_document_id in used_sources,
                pending[i].target_document_id in used_targets,
                i,
            ),
        )
        edge = pending.pop(position)
        source, target = nodes[edge.source_document_id], nodes[edge.target_document_id]
        document = parsed.get(source.document_id, {})
        for reference in document.get("references", []):
            if reference.get("reference_id") not in edge.reference_ids:
                continue
            quote = str(reference.get("text") or "").strip()
            compact_quote = _compact(quote)
            arxiv_ids = {match.group(1).lower() for match in ARXIV_ID.finditer(quote)}
            matches = [
                node
                for node in graph.nodes
                if node.document_id != source.document_id
                and (
                    len(_compact(node.title)) >= 10
                    and _compact(node.title) in compact_quote
                    or node.arxiv_id
                    and node.arxiv_id.lower() in arxiv_ids
                )
            ]
            if len(matches) != 1 or matches[0].document_id != target.document_id:
                continue
            page_number = reference.get("page_number")
            page = next(
                (
                    page
                    for page in document.get("pages", [])
                    if page.get("page_number") == page_number
                ),
                {},
            )
            ids = reference.get("block_ids") or []
            blocks = {block.get("block_id"): block for block in page.get("blocks", [])}
            if (
                not isinstance(page_number, int)
                or page_number < 1
                or not ids
                or any(block_id not in blocks for block_id in ids)
                or not quote
                or len(quote) > 1600
                or compact_quote
                not in _compact(
                    " ".join(str(blocks[block_id].get("text") or "") for block_id in ids)
                )
            ):
                continue
            bbox = blocks[ids[0]].get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            key = f"G{len(relations) + 1}"
            evidence.append(
                ReviewEvidenceRead(
                    evidence_id=key,
                    document_id=source.document_id,
                    document_title=source.title,
                    page_number=page_number,
                    block_ids=ids,
                    bbox=bbox,
                    section="References",
                    quote=quote,
                    source_type="reference",
                    score=edge.match_score,
                    evidence_kind="citation_relation",
                )
            )
            relations.append(
                ReviewCitationRelationRead(
                    source_document_id=source.document_id,
                    source_title=source.title,
                    target_document_id=target.document_id,
                    target_title=target.title,
                    evidence_id=key,
                    match_reason="原页参考条目与所选题名／arXiv 身份明确匹配",
                )
            )
            used_sources.add(source.document_id)
            used_targets.add(target.document_id)
            break
    return evidence, relations
