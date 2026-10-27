"""Group only published claims; themes never resurrect rejected statements."""

from app.schemas.review import ReviewThemeRead
from app.services.grounded_text import INLINE_ANCHOR


def published_review_themes(generated, claims, evidence):
    anchors = {e.evidence_id: e for e in evidence}
    metadata = {}
    for item in generated:
        if not item.review_topic:
            continue
        key = (INLINE_ANCHOR.sub("", item.text).strip(), frozenset(item.evidence_ids))
        metadata.setdefault(key, item.review_topic)
    groups = {}
    for index, claim in enumerate(claims):
        title = metadata.get((claim.text, frozenset(claim.evidence_ids)))
        if title is None:
            continue
        group = groups.setdefault(title, ReviewThemeRead(title=title, claim_indices=[]))
        group.claim_indices.append(index)
        if (
            len(
                {
                    anchors[key].document_id
                    for key in claim.evidence_ids
                    if key in anchors and anchors[key].source_type != "reference"
                }
            )
            >= 2
        ):
            group.cross_paper_claim_indices.append(index)
    return list(groups.values())
