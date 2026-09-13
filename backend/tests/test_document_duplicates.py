from app.services.document_duplicates import assess_duplicate, recommend_version
from app.services.fingerprints import bottom_k_signature


def test_same_arxiv_versions_qualify_and_keep_newer_existing_version() -> None:
    shared = "transformer attention encoder decoder representation learning " * 80
    assessment = assess_duplicate(
        current_arxiv_id="1706.03762",
        current_title="Attention Is All You Need",
        current_signature=bottom_k_signature(shared + "original appendix"),
        existing_arxiv_id="1706.03762",
        existing_title="Attention Is All You Need",
        existing_signature=bottom_k_signature(shared + "revised appendix"),
    )

    assert assessment.qualifies is True
    assert assessment.same_arxiv is True
    assert assessment.content_score >= 0.30
    assert recommend_version(1, 7).startswith("库中已有更新的 arXiv v7")


def test_unrelated_documents_do_not_qualify() -> None:
    assessment = assess_duplicate(
        current_arxiv_id=None,
        current_title="Attention Networks",
        current_signature=bottom_k_signature("attention transformer language " * 40),
        existing_arxiv_id=None,
        existing_title="Protein Structure Prediction",
        existing_signature=bottom_k_signature("protein amino acid folding " * 40),
    )

    assert assessment.qualifies is False
