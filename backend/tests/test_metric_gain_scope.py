import pytest

from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor
from app.services.grounded_text import audit_grounded_claims, grounding_rejection_warnings

SOURCE = ("Figure 3: Mean zero-shot accuracy using different data types. "
          "While quantization only leads to minor gains, it allows for finer control over "
          "the memory footprint. Quantization reduces memory without degrading performance.")


def anchor(quote=SOURCE, key="E1", source_type="text"):
    return EvidenceAnchor(evidence_id=key, document_id="paper", page_number=1,
                          quote=quote, source_type=source_type, score=1)


def audit(text, evidence=None, ids=None):
    return audit_grounded_claims([GeneratedClaim(text, ids or ["E1"])], evidence or [anchor()])


@pytest.mark.parametrize("text", [
    "量化通过量化常数降低内存占用；内存收益较小，但不降低性能。",
    "内存节省很小。",
    "Memory savings are minor.",
    "The method gives small memory savings.",
])
def test_accuracy_gain_cannot_become_a_memory_saving_magnitude(text):
    accepted, report = audit(text)
    assert not accepted
    assert "performance_gain_not_memory_gain" in str(report.model_dump())
    assert "准确率增益" in grounding_rejection_warnings(report)[0]


@pytest.mark.parametrize("text", [
    "该方法降低平均内存占用，并允许更细粒度控制内存足迹。",
    "图中的准确率增益较小，同时该方法可节省内存。",
    "不能据此把准确率提升小写成内存收益较小。",
])
def test_correct_metric_and_unquantified_memory_mechanism_survive_unchanged(text):
    accepted, _ = audit(text)
    assert len(accepted) == 1 and accepted[0].text == text


def test_explicit_memory_statement_counts_only_when_actually_cited():
    direct = anchor("Memory savings are minor in this experiment.", "E2")
    evidence = [anchor(), direct]
    assert not audit("Memory savings are minor.", evidence)[0]
    assert audit("Memory savings are minor.", evidence, ["E1", "E2"])[0]
    assert audit("Memory savings are minor.", [anchor(source_type="reference")])[0]


def test_bad_metric_does_not_remove_other_supported_claims():
    accepted, report = audit_grounded_claims([
        GeneratedClaim("内存收益较小。", ["E1"]),
        GeneratedClaim("该方法减少平均内存占用。", ["E1"]),
    ], [anchor()])
    assert len(report.rejections) == 1 and len(accepted) == 1
    assert accepted[0].text == "该方法减少平均内存占用。"
