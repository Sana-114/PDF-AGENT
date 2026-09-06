from app.services.fingerprints import (
    bottom_k_signature,
    extract_arxiv_identity,
    signature_similarity,
    title_similarity,
)


def test_extract_arxiv_identity() -> None:
    assert extract_arxiv_identity("arXiv:1706.03762v7 [cs.CL]") == ("1706.03762", 7)


def test_related_text_has_higher_signature_similarity() -> None:
    base = "attention mechanism transformer encoder decoder " * 30
    related = base + "additional appendix experiments " * 5
    unrelated = "protein folding molecular biology cells " * 30

    base_signature = bottom_k_signature(base)
    assert signature_similarity(base_signature, bottom_k_signature(related)) > signature_similarity(
        base_signature, bottom_k_signature(unrelated)
    )


def test_title_similarity_normalizes_case_and_spacing() -> None:
    assert title_similarity("Attention Is All You Need", " attention  is ALL you need ") == 1.0

