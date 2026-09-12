from typing import Protocol

from app.schemas.agent import EvidenceAnchor


class Reranker(Protocol):
    name: str
    model: str
    enabled: bool
    candidate_k: int

    def rerank(
        self, question: str, evidence: list[EvidenceAnchor], top_k: int
    ) -> list[EvidenceAnchor]: ...
