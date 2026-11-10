import pytest

from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor
from app.services.grounded_text import audit_grounded_claims


@pytest.mark.parametrize("claim,allowed", [
    ("该研究训练了少于 1,000 个模型。", False),
    ("该研究训练了超过 1,000 个模型。", True),
    ("The study trained fewer than 1000 models.", False),
    ("The study trained over 1000 models.", True),
])
def test_model_count_direction_is_part_of_the_fact(claim, allowed):
    evidence = [EvidenceAnchor(evidence_id="E1", document_id="public", page_number=1,
                              quote="We finetune more than 1,000 models.", score=1)]
    accepted, audit = audit_grounded_claims([GeneratedClaim(claim, ["E1"])], evidence)
    assert bool(accepted) == allowed
    if not allowed:
        assert "semantic_conflict:reversed_model_count_bound" in audit.rejections[0].reasons
