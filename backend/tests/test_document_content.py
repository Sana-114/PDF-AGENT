from app.services.document_content import outline_items, page_text_segments, reference_links


def test_outline_items_preserves_nested_document_ast() -> None:
    nested = [
        {
            "text": "1 Introduction",
            "level": 1,
            "page_number": 2,
            "block_id": "p2-b1",
            "bbox": [72.0, 90.0, 240.0, 110.0],
            "children": [
                {
                    "text": "1.1 Motivation",
                    "level": 2,
                    "page_number": 3,
                    "block_id": "p3-b1",
                    "bbox": [72.0, 90.0, 240.0, 110.0],
                    "children": [],
                }
            ],
        }
    ]

    assert outline_items({"outline": nested}) == nested


def test_outline_items_builds_flat_legacy_fallback() -> None:
    parsed = {
        "outline": [],
        "pages": [
            {
                "page_number": 4,
                "blocks": [
                    {
                        "block_id": "p4-b2",
                        "type": "heading",
                        "text": "2 Results",
                        "level": 1,
                        "bbox": [72.0, 120.0, 230.0, 143.0],
                    },
                    {"block_id": "p4-b3", "type": "text", "text": "Body"},
                ],
            }
        ],
    }

    assert outline_items(parsed) == [
        {
            "text": "2 Results",
            "level": 1,
            "page_number": 4,
            "block_id": "p4-b2",
            "bbox": [72.0, 120.0, 230.0, 143.0],
            "children": [],
        }
    ]


def test_reference_links_match_numeric_citations_and_skip_reference_blocks() -> None:
    parsed = {
        "references": [
            {
                "reference_id": "ref-1",
                "label": "1",
                "text": "First paper.",
                "page_number": 8,
                "bbox": [60, 100, 500, 130],
                "block_ids": ["p8-b2"],
            },
            {
                "reference_id": "ref-2",
                "label": "2",
                "text": "Second paper.",
                "page_number": 8,
                "bbox": [60, 140, 500, 170],
                "block_ids": ["p8-b3"],
            },
        ],
        "pages": [
            {
                "page_number": 2,
                "blocks": [
                    {
                        "block_id": "p2-b4",
                        "text": "Prior work [1] and related methods [1–3] are compared.",
                        "bbox": [60, 200, 500, 250],
                    }
                ],
            },
            {
                "page_number": 8,
                "blocks": [
                    {
                        "block_id": "p8-b2",
                        "text": "First paper cites [2].",
                        "bbox": [60, 100, 500, 130],
                    }
                ],
            },
        ],
    }

    references, mentions = reference_links(parsed)

    assert [item["label"] for item in references] == ["1", "2"]
    assert [(item["label"], item["page_number"]) for item in mentions] == [
        ("1", 2),
        ("1", 2),
        ("2", 2),
    ]
    assert all(item["block_id"] == "p2-b4" for item in mentions)


def test_page_text_segments_preserve_block_alignment_and_enforce_budget() -> None:
    parsed = {
        "pages": [
            {
                "page_number": 3,
                "blocks": [
                    {"block_id": "p3-b1", "text": "  Heading  ", "bbox": [1, 2, 3, 4]},
                    {"block_id": "p3-empty", "text": "  ", "bbox": None},
                    {"block_id": "p3-b2", "text": "Long paragraph", "bbox": [5, 6, 7, 8]},
                ],
            }
        ]
    }

    segments, truncated = page_text_segments(parsed, 3, max_characters=10)

    assert segments == [
        {"block_id": "p3-b1", "bbox": [1, 2, 3, 4], "source_text": "Heading"}
    ]
    assert truncated is True
    assert page_text_segments(parsed, 9) == ([], False)
