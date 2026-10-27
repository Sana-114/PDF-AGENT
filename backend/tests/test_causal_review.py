import asyncio

import pytest

from app.llm.base import GeneratedClaim, LLMResponseError
from app.schemas.agent import EvidenceAnchor
from app.services.relation_review import review_relational_claims as review_causal_claims


class Reviewer:
    def __init__(self, verdicts):
        self.verdicts, self.calls = verdicts, 0

    async def review_claims(self, claims, evidence):
        self.calls += 1
        self.received = claims, evidence
        return self.verdicts


def test_causal_details_reviewed_using_original_citations_and_rejected_without_rewrite():
    causal = GeneratedClaim("省去在线采样，因此免去超参数调优。", ["E1"])
    factual = GeneratedClaim("使用分类目标。", ["E2"])
    source = [EvidenceAnchor(evidence_id="E1", document_id="paper", page_number=1,
                             quote="No online sampling or significant tuning.", score=1)]
    reviewer = Reviewer([
        {"claim_index": 0, "status": "unsupported", "reason": "两项并列，无因果依据"}
    ])
    kept, audit = asyncio.run(review_causal_claims(reviewer, [factual, causal], source))
    assert kept == [factual] and causal.text == "省去在线采样，因此免去超参数调优。"
    assert audit["claim_indices"] == [1] and reviewer.calls == 1
    assert reviewer.received == ([causal], source)


def test_supported_causal_statement_survives_and_noncausal_answer_makes_no_call():
    claim = GeneratedClaim("This enables parallel training.", ["E1"])
    reviewer = Reviewer([{"claim_index": 0, "status": "supported", "reason": "原文直接说明"}])
    kept, _ = asyncio.run(review_causal_claims(reviewer, [claim], []))
    assert kept == [claim] and reviewer.calls == 1
    factual = GeneratedClaim("Uses parallel training.", ["E1"])
    assert asyncio.run(review_causal_claims(reviewer, [factual], [])) == ([factual], None)
    assert reviewer.calls == 1


@pytest.mark.parametrize("broken", ["duplicate", "unavailable"])
def test_failed_review_withholds_only_causal_claims_without_retry(broken):
    class Broken(Reviewer):
        async def review_claims(self, claims, evidence):
            self.calls += 1
            if broken == "unavailable":
                raise LLMResponseError("offline")
            return []
    reviewer = Broken([])
    factual = GeneratedClaim("Reports a classification objective.", ["E1"])
    causal = GeneratedClaim("This therefore removes all tuning.", ["E1"])
    kept, audit = asyncio.run(review_causal_claims(reviewer, [factual, causal], []))
    assert kept == [factual] and audit["status"] == "unavailable" and reviewer.calls == 1


@pytest.mark.parametrize("statement", [
    "最优策略可直接由策略提取。",
    "The closed-form result gives learned neural network parameters.",
    "该重参数化与原目标在任意条件下等价。",
])
def test_mathematical_relation_without_causal_keyword_requires_own_review(statement):
    claim = GeneratedClaim(statement, ["E1"])
    plain = GeneratedClaim("报告了训练样本数量。", ["E2"])
    reviewer = Reviewer([{"claim_index": 0, "status": "unsupported", "reason": "对象或条件不符"}])
    kept, audit = asyncio.run(review_causal_claims(reviewer, [plain, claim], []))
    assert kept == [plain]
    assert audit["claim_indices"] == [1] and reviewer.calls == 1


def test_supported_mathematical_relation_preserves_original_wording_and_citations():
    claim = GeneratedClaim(
        "The optimal policy has a closed-form expression for a given reward.", ["E3"],
    )
    reviewer = Reviewer([
        {"claim_index": 0, "status": "supported", "reason": "原文明确给出对象与条件"}
    ])
    kept, _ = asyncio.run(review_causal_claims(reviewer, [claim], []))
    assert kept == [claim] and kept[0] is claim and reviewer.calls == 1
