"""Add bounded, source-anchored continuations to writing evidence.

This is opt-in for outline drafting. A continuation keeps its own physical page
and block; it never replaces the original retrieved quote or its citation ID.
"""

import re
import unicodedata
from collections.abc import Sequence

from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.document_content import block_sections, read_parsed_document

MAX_CONTEXT_BLOCKS = 10
MAX_CONTEXT_CHARS = 9500
SECTION_HEADING = re.compile(
    r"(?m)^(?:[A-Z]\.\d+(?:\.\d+)*|\d+(?:\.\d+)*)\s+[A-Z][A-Z /&()\-]{4,}$"
)
DEPENDENT_PARAGRAPH = re.compile(
    r"^(?:however|instead|but|yet|nevertheless|therefore|thus|although|"
    r"this|these|that|those|it|they|in contrast|as a result)\b",
    re.I,
)
TOPIC_STOPWORDS = frozenset(
    "a an and are as at be been being but by can could did do does for from had has "
    "have how if in into is it its may might more most nor not of on only or our "
    "paper per than that the their them then there these they this those through "
    "to use used uses using was we were what when where which while who why will "
    "with without would model models study studies result results evidence "
    "provided supplied contain contains finding findings".split()
)
EVALUATION_CONTRAST = re.compile(
    r"\b(?:report|use|measure)\b[^.!?]{0,80}\bfew[-\s]*shot\s+linear\s+"
    r"(?:accuracy|evaluation)[^.!?]{0,30}\binstead of\s+(?:the\s+)?full\s+fine[-\s]*tuning",
    re.I,
)
PERFORMANCE_STATEMENT = re.compile(
    r"\b(?:(?:out|under)?perform(?:ance|s|ed|ing)?|accuracy|overfit\w*|better|worse)\b|"
    r"表现|性能|准确率|优于|过拟合", re.I,
)


def missing_explicit_evaluation_protocol(
    text: str, evidence: Sequence[EvidenceAnchor],
) -> bool:
    """Hold a performance claim omitting a source's explicit protocol contrast.

    This narrow few-shot linear/full-fine-tuning cue is not general entailment.
    Method-only facts need no performance label; no label is added to a claim.
    """
    claim = unicodedata.normalize("NFKC", text)
    if not PERFORMANCE_STATEMENT.search(claim):
        return False
    contrast = any(
        item.source_type != "reference"
        and EVALUATION_CONTRAST.search(unicodedata.normalize("NFKC", " ".join(item.quote.split())))
        for item in evidence
    )
    if not contrast:
        return False
    english_label = re.search(r"\bfew[-\s]*shot\b", claim, re.I) and re.search(
        r"\blinear\b", claim, re.I,
    )
    chinese_label = re.search(r"(?:少|小)样本", claim) and "线性" in claim
    return not bool(english_label or chinese_label)


def _normalized(text: str) -> str:
    return re.sub(r"\W+", "", unicodedata.normalize("NFKC", text)).casefold()


def _prose(block: dict, reference_ids: set[str], page_number: int) -> bool:
    text = str(block.get("text") or "").strip()
    if block.get("block_id") in reference_ids or block.get("type") not in {
        "text",
        "figure_caption",
    }:
        return False
    if len(text) < 65:
        return False
    bbox = block.get("bbox")
    if (
        isinstance(bbox, list)
        and len(bbox) == 4
        and isinstance(bbox[1], int | float)
        and bbox[1] < 70
        and text.endswith(f" {page_number}")
    ):
        return False
    return sum(char.isalpha() for char in text) / len(text) >= 0.5


def _candidate_blocks(parsed: dict, anchor: EvidenceAnchor) -> list[tuple[int, dict]]:
    pages = {int(page.get("page_number", 0)): page for page in parsed.get("pages", [])}
    page = pages.get(anchor.page_number)
    if not isinstance(page, dict):
        return []
    reference_ids = {
        str(block_id)
        for ref in parsed.get("references", [])
        if isinstance(ref, dict)
        for block_id in ref.get("block_ids", [])
    }
    if set(anchor.block_ids) & reference_ids:
        return []
    blocks = page.get("blocks", [])
    visible = [
        block
        for block in blocks
        if isinstance(block, dict) and _prose(block, reference_ids, anchor.page_number)
    ]
    quote = _normalized(anchor.quote)
    matching = [
        index
        for index, block in enumerate(visible)
        if block.get("block_id") in anchor.block_ids
        or (
            len(_normalized(str(block.get("text") or ""))) >= 50
            and _normalized(str(block.get("text") or "")) in quote
        )
    ]
    if not matching:
        return []
    candidates: list[tuple[int, dict]] = []
    if max(matching) >= len(visible) - 2:
        next_page = pages.get(anchor.page_number + 1)
        if isinstance(next_page, dict):
            for block in next_page.get("blocks", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "heading":
                    break
                if _prose(block, reference_ids, anchor.page_number + 1):
                    candidates.append((anchor.page_number + 1, block))
                    if len(candidates) == 2:
                        break
    for index in (max(matching) + 1, min(matching) - 1):
        if 0 <= index < len(visible):
            candidates.append((anchor.page_number, visible[index]))
    return candidates


def _abstract_blocks(parsed: dict) -> list[tuple[int, dict]]:
    """Find real abstract body blocks, never synthesize text from a title.

    Only a visible abstract heading in the first two pages establishes scope.
    Each complete body block keeps its original page and geometry. A missing
    heading is not replaced by a guessed introduction or metadata summary.
    """
    reference_ids = {str(key) for ref in parsed.get("references", [])
                     if isinstance(ref, dict) for key in ref.get("block_ids", [])}
    active = False
    found = []
    for page in parsed.get("pages", [])[:2]:
        number = int(page.get("page_number", 0))
        for block in page.get("blocks", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "heading":
                if re.fullmatch(r"abstract|摘要", str(block.get("text", "")).strip(), re.I):
                    active = True
                elif active:
                    return found
                continue
            if active and block.get("type") == "text" and _prose(block, reference_ids, number):
                found.append((number, block))
                if len(found) == 2:
                    return found
    return found


def _unfinished_tail(quote: str) -> bool:
    tail = quote.strip().rstrip("| 0123456789\n\r\t")
    return bool(tail) and not tail.endswith((".", "。", "!", "?"))


def _separate_unfinished_passage(quote: str) -> tuple[str, str] | None:
    """Find a visible section/paragraph boundary, not an inferred topic change.

    Flatten table cell decoration but retain physical newlines. Many PDF chunks
    include several sections; a separate, completed section is not made unsafe
    just because the final section continues on another page. If there is no
    reliable boundary, leave the conservative citation requirement in place.
    """

    lines = [line.replace("|", " ").strip() for line in quote.splitlines()]
    text = "\n".join(line for line in lines if line and not re.fullmatch(r"[\d\s:\-]+", line))
    text = unicodedata.normalize("NFKC", text)
    boundaries = [match.start() for match in SECTION_HEADING.finditer(text)]
    # Keep the whole last section, including its initial experiment and later
    # caveats. A newline after "performed poorly." is not a new subject.
    for position in sorted(set(boundaries), reverse=True):
        prefix, tail = text[:position].strip(), text[position:].strip()
        if len(prefix) >= 65 and len(tail) >= 65 and _unfinished_tail(tail):
            return prefix, tail
    # A newline after a completed sentence can separate paragraphs. A normal
    # PDF word wrap does not meet this condition. Keep only substantial tails.
    boundaries = [match.end() for match in re.finditer(r"[.!?。]\s*\n", text)]
    for position in sorted(set(boundaries), reverse=True):
        prefix, tail = text[:position].strip(), text[position:].strip()
        if (
            len(prefix) >= 65
            and len(tail) >= 65
            and _unfinished_tail(tail)
            and not DEPENDENT_PARAGRAPH.match(tail)
        ):
            return prefix, tail
    return None


def _topic_words(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    words = {word.casefold() for word in re.findall(r"[A-Za-z][A-Za-z0-9]*", text) if len(word) > 2}
    return {
        word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word
        for word in words - TOPIC_STOPWORDS
    }


def _clearly_uses_completed_passage(
    claim: AnswerClaim, root: EvidenceAnchor, cited: Sequence[EvidenceAnchor]
) -> bool:
    """A narrow opt-out for English claims anchored to separate finished prose.

    This detects where a claim draws its terms from, not factual entailment.
    Unknown/cross-language paraphrases remain held for review. Any distinctive
    term from the unfinished passage keeps the continuation requirement.
    """

    separated = _separate_unfinished_passage(root.quote)
    if separated is None or re.search(r"[\u4e00-\u9fff]", claim.text):
        return False
    prefix, tail = separated
    claim_words = _topic_words(claim.text)
    if len(claim_words) < 5:
        return False
    completed = _topic_words(prefix)
    pending = _topic_words(tail)
    # Other citations can support parts of the same compound statement, but
    # cannot erase a distinctive match to this root's unfinished paragraph.
    other = set().union(
        *(_topic_words(item.quote) for item in cited if item.evidence_id != root.evidence_id)
    )
    supported = completed | other
    distinct_complete = claim_words & (completed - pending)
    distinct_pending = claim_words & (pending - completed)
    return (
        len(distinct_complete) >= 4
        and not distinct_pending
        and len(claim_words & supported) / len(claim_words) >= 0.75
        and len(claim_words & pending) / len(claim_words) < 0.3
    )


def expand_writing_evidence(evidence: Sequence[EvidenceAnchor]) -> list[EvidenceAnchor]:
    """Append at most ten source blocks, prioritizing necessary continuations.

    Abstracts provide methodological overview, neighbors provide local details;
    neither changes original ranked hits or silently enlarges the document scope.
    """

    original = list(evidence)
    used_ids = {item.evidence_id for item in original}
    existing_blocks = {
        (item.document_id, item.page_number, block_id)
        for item in original
        for block_id in item.block_ids
    }
    original_positions: dict[tuple[str, int, str], list[int]] = {}
    for index, item in enumerate(original):
        for block_id in item.block_ids:
            original_positions.setdefault(
                (item.document_id, item.page_number, block_id), []
            ).append(index)
    next_id = (
        max(
            (
                int(match.group(1))
                for item in original
                if (match := re.fullmatch(r"E(\d+)", item.evidence_id))
            ),
            default=0,
        )
        + 1
    )
    additions: list[EvidenceAnchor] = []
    added_chars = 0
    by_document: dict[str, dict | None] = {}
    section_maps: dict[str, dict[tuple[int, str], str | None]] = {}
    candidate_groups: list[tuple[int, EvidenceAnchor, list[tuple[int, dict]], str]] = []
    abstract_documents: set[str] = set()
    for anchor in original:
        if anchor.source_type == "reference" or anchor.context_for:
            continue
        if anchor.document_id not in by_document:
            by_document[anchor.document_id] = read_parsed_document(anchor.document_id)
        parsed = by_document[anchor.document_id]
        if not isinstance(parsed, dict):
            continue
        section_maps.setdefault(anchor.document_id, block_sections(parsed))
        if anchor.document_id not in abstract_documents:
            abstract_documents.add(anchor.document_id)
            candidate_groups.append((1, anchor, _abstract_blocks(parsed), "abstract"))
        candidates = _candidate_blocks(parsed, anchor)
        if not candidates:
            continue
        next_page = any(page == anchor.page_number + 1 for page, _ in candidates)
        # A page break after an unfinished sentence can reverse the apparent
        # result. Spend the limited continuation budget on these first.
        incomplete = _unfinished_tail(anchor.quote)
        priority = 0 if next_page and incomplete else 2 if next_page else 3
        candidate_groups.append((priority, anchor, candidates, "neighbor"))
    for _, anchor, candidates, kind in sorted(candidate_groups, key=lambda group: group[0]):
        for page_number, block in candidates:
            block_id = str(block.get("block_id") or "")
            text = str(block.get("text") or "").strip()
            key = (anchor.document_id, page_number, block_id)
            sections = section_maps.get(anchor.document_id, {})
            section = sections.get((page_number, block_id))
            root_sections = {sections.get((anchor.page_number, key)) for key in anchor.block_ids}
            root_sections.discard(None)
            independent = kind == "abstract" or bool(
                section and root_sections and section not in root_sections
            )
            if key in existing_blocks:
                # The next-page block may already be a ranked hit. Preserve
                # its rank/quote/anchor and record the relation instead of
                # silently dropping the continuation requirement.
                if not independent and kind == "neighbor" and page_number == anchor.page_number + 1:
                    for position in original_positions.get(key, []):
                        existing = original[position]
                        original[position] = existing.model_copy(
                            update={
                                "context_for": list(
                                    dict.fromkeys([*existing.context_for, anchor.evidence_id])
                                )
                            }
                        )
                    for position, existing in enumerate(additions):
                        if block_id in existing.block_ids and (
                            existing.document_id,
                            existing.page_number,
                        ) == (anchor.document_id, page_number):
                            additions[position] = existing.model_copy(
                                update={
                                    "context_for": list(
                                        dict.fromkeys([*existing.context_for, anchor.evidence_id])
                                    )
                                }
                            )
                continue
            if not block_id or added_chars + len(text) > MAX_CONTEXT_CHARS:
                continue
            if len(additions) >= MAX_CONTEXT_BLOCKS:
                return original + additions
            while f"E{next_id}" in used_ids:
                next_id += 1
            evidence_id = f"E{next_id}"
            used_ids.add(evidence_id)
            next_id += 1
            bbox = block.get("bbox")
            additions.append(
                EvidenceAnchor(
                    evidence_id=evidence_id,
                    document_id=anchor.document_id,
                    document_title=anchor.document_title,
                    page_number=page_number,
                    block_ids=[block_id],
                    bbox=bbox if isinstance(bbox, list) and len(bbox) == 4 else None,
                    section="Abstract" if kind == "abstract" else section,
                    source_type="abstract" if kind == "abstract"
                    else "figure" if block.get("type") == "figure_caption" else "text",
                    retrieval_mode="local_context" if independent else anchor.retrieval_mode,
                    recall_mode=None if independent else anchor.recall_mode,
                    quote=text,
                    score=0.0 if independent else anchor.score,
                    context_for=[] if independent else [anchor.evidence_id],
                )
            )
            existing_blocks.add(key)
            added_chars += len(text)
    return original + additions


def withhold_unqualified_continuation_claims(
    claims: Sequence[AnswerClaim], evidence: Sequence[EvidenceAnchor]
) -> tuple[list[AnswerClaim], int]:
    """Require next-page citations for claims using unfinished passages.

    Clearly separate finished prose may stand alone. Ambiguous dependency is
    still held for review; neither the statement nor its citations is rewritten.
    """

    anchors = {item.evidence_id: item for item in evidence}
    continuation_ids: dict[str, set[str]] = {}
    for item in evidence:
        for root_id in item.context_for:
            root = anchors.get(root_id)
            if (
                root is not None
                and item.document_id == root.document_id
                and item.page_number == root.page_number + 1
            ):
                continuation_ids.setdefault(root_id, set()).add(item.evidence_id)
    published: list[AnswerClaim] = []
    withheld = 0
    for claim in claims:
        cited = set(claim.evidence_ids)
        cited_anchors = [anchors[item] for item in cited if item in anchors]
        missing = any(
            root_id in cited
            and _unfinished_tail(root.quote)
            and not (cited & continuation_ids[root_id])
            and not _clearly_uses_completed_passage(claim, root, cited_anchors)
            for root_id, root in anchors.items()
            if root_id in continuation_ids
        )
        if missing:
            withheld += 1
        else:
            published.append(claim)
    return published, withheld
