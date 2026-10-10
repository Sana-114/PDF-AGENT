import pytest
from pydantic import ValidationError

from app.schemas.review import ResearchReviewRequest
from app.schemas.writing import WritingOutlineRequest


@pytest.mark.parametrize("payload", [{}, {"document_ids": []}])
def test_generative_review_requires_explicit_nonempty_scope(payload):
    with pytest.raises(ValidationError, match="必须明确选择"):
        ResearchReviewRequest.model_validate(payload)


@pytest.mark.parametrize("payload", [{}, {"document_ids": []}])
def test_generative_writing_requires_explicit_nonempty_scope(payload):
    with pytest.raises(ValidationError):
        WritingOutlineRequest.model_validate({"idea": "A grounded writing idea", **payload})


def test_local_extractive_review_can_still_use_library_without_cloud_call():
    assert ResearchReviewRequest(generation_mode="extractive").document_ids == []


def test_generation_scope_deduplicates_only_explicit_ids():
    assert ResearchReviewRequest(document_ids=["public", "public"]).document_ids == ["public"]
    assert WritingOutlineRequest(
        idea="A grounded writing idea", document_ids=["public", "public"]
    ).document_ids == ["public"]
