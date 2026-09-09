from app.services.document_content import outline_items


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
