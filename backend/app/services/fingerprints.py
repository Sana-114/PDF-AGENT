import hashlib
import heapq
import json
import re
from difflib import SequenceMatcher

TOKEN_PATTERN = re.compile(r"[\w]+", re.UNICODE)
ARXIV_PATTERN = re.compile(
    r"(?:arxiv\s*:\s*)?(?P<id>\d{4}\.\d{4,5})(?:v(?P<version>\d+))?",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    return " ".join(TOKEN_PATTERN.findall(text.casefold()))


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    return normalize_text(title)


def title_similarity(left: str | None, right: str | None) -> float:
    normalized_left = normalize_title(left)
    normalized_right = normalize_title(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def extract_arxiv_identity(text: str) -> tuple[str | None, int | None]:
    match = ARXIV_PATTERN.search(text[:20_000])
    if not match:
        return None, None
    version = match.group("version")
    return match.group("id"), int(version) if version else None


def extract_document_arxiv_identity(
    filename: str | None, text: str
) -> tuple[str | None, int | None]:
    """Prefer an explicit source filename, then inspect the parsed document text."""

    identity = extract_arxiv_identity(filename or "")
    return identity if identity[0] else extract_arxiv_identity(text)


def bottom_k_signature(text: str, *, shingle_size: int = 5, size: int = 128) -> list[int]:
    """Return a compact, deterministic sketch for approximate content overlap."""
    tokens = normalize_text(text).split()
    if not tokens:
        return []

    if len(tokens) < shingle_size:
        shingles = [" ".join(tokens)]
    else:
        shingles = (
            " ".join(tokens[index : index + shingle_size])
            for index in range(len(tokens) - shingle_size + 1)
        )

    heap: list[int] = []
    seen: set[int] = set()
    for shingle in shingles:
        value = int.from_bytes(
            hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big"
        )
        if value in seen:
            continue
        seen.add(value)
        if len(heap) < size:
            heapq.heappush(heap, -value)
        elif value < -heap[0]:
            removed = -heapq.heapreplace(heap, -value)
            seen.discard(removed)

    return sorted(-value for value in heap)


def signature_to_json(signature: list[int]) -> str:
    return json.dumps(signature, separators=(",", ":"))


def signature_from_json(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(item) for item in json.loads(value)]


def signature_similarity(left: list[int], right: list[int]) -> float:
    """Estimate overlap using the Jaccard ratio of two bottom-k sketches."""
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    return len(left_set & right_set) / len(left_set | right_set)
