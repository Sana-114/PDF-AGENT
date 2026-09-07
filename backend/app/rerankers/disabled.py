from app.schemas.agent import EvidenceAnchor


class DisabledReranker:
    name = "disabled"
    model = ""
    enabled = False
    candidate_k = 0

    def rerank(
        self, question: str, evidence: list[EvidenceAnchor], top_k: int
    ) -> list[EvidenceAnchor]:
        del question
        return evidence[:top_k]
