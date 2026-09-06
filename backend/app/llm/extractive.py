from app.llm.base import GeneratedAnswer, GeneratedClaim
from app.schemas.agent import EvidenceAnchor


class ExtractiveProvider:
    """Deterministic, no-key fallback that only restates retrieved passages."""

    name = "extractive"
    model = None

    async def generate_grounded_answer(
        self, question: str, evidence: list[EvidenceAnchor]
    ) -> GeneratedAnswer:
        del question
        if not evidence:
            return GeneratedAnswer(
                answer="当前文献中没有检索到足以回答该问题的证据。",
                claims=[],
            )

        selected = evidence[:3]
        claims = [
            GeneratedClaim(text=item.quote, evidence_ids=[item.evidence_id]) for item in selected
        ]
        answer = "\n\n".join(
            f"{item.quote} [{item.evidence_id}]" for item in selected
        )
        return GeneratedAnswer(answer=answer, claims=claims)

