from types import SimpleNamespace

from app.services.document_version_diff import compare_document_versions


def _parsed(
    *,
    heading: str,
    paragraph: str,
    page_count: int,
    tables: int,
) -> dict:
    return {
        "outline": [
            {
                "text": "1 Introduction",
                "level": 1,
                "page_number": 1,
                "children": [
                    {
                        "text": heading,
                        "level": 2,
                        "page_number": 2,
                        "children": [],
                    }
                ],
            }
        ],
        "pages": [
            {
                "page_number": page_number,
                "blocks": [
                    {
                        "block_id": f"p{page_number}-b1",
                        "type": "text",
                        "text": paragraph if page_number == 2 else "Shared introduction text.",
                    }
                ],
            }
            for page_number in range(1, page_count + 1)
        ],
        "tables": [{} for _ in range(tables)],
        "figures": [],
        "formulas": [],
        "references": [{}, {}],
        "appendices": [],
    }


def test_version_diff_reports_structure_and_anchored_revision_evidence() -> None:
    existing = SimpleNamespace(
        id="version-1",
        original_filename="1706.03762v1.pdf",
        arxiv_version=1,
        page_count=2,
    )
    current = SimpleNamespace(
        id="version-7",
        original_filename="1706.03762v7.pdf",
        arxiv_version=7,
        page_count=3,
    )
    existing_paragraph = (
        "The original evaluation reports baseline translation scores on a compact "
        "dataset with the initial training configuration and limited ablations."
    )
    current_paragraph = (
        "The revised evaluation adds multilingual benchmarks, adversarial robustness "
        "analysis, calibration experiments, and a detailed ablation of attention heads."
    )

    result = compare_document_versions(
        current=current,
        existing=existing,
        current_parsed=_parsed(
            heading="2.1 Robustness Experiments",
            paragraph=current_paragraph,
            page_count=3,
            tables=2,
        ),
        existing_parsed=_parsed(
            heading="2.1 Baseline Evaluation",
            paragraph=existing_paragraph,
            page_count=2,
            tables=1,
        ),
    )

    assert result["current_label"] == "v7"
    assert result["existing_label"] == "v1"
    assert result["page_delta"] == 1
    assert result["structure_deltas"]["tables"] == 1
    assert result["added_headings"] == [
        {"text": "2.1 Robustness Experiments", "page_number": 2, "level": 2}
    ]
    assert result["removed_headings"] == [
        {"text": "2.1 Baseline Evaluation", "page_number": 2, "level": 2}
    ]
    assert result["possibly_added_passages"][0]["page_number"] == 2
    assert result["possibly_added_passages"][0]["block_id"] == "p2-b1"
    assert "adversarial robustness" in result["possibly_added_passages"][0]["text"]
    assert result["possibly_removed_passages"][0]["page_number"] == 2
    assert 0 <= result["content_overlap"] <= 1
    assert all("语义等价" not in line or "不代表" in line for line in result["summary"])
