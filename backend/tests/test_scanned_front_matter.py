from app.parsers.layout import RawTextBlock
from app.parsers.people import extract_front_matter_people


def _raw(text: str, y: int, lines: list[dict] | None = None) -> RawTextBlock:
    return RawTextBlock(1, [60, y, 500, y + 15], text, 10, 1, len(text), lines or [])


def _grid_lines(texts: list[str]) -> list[dict]:
    return [
        {"text": text, "bbox": [60, 100 + i * 12, 500, 110 + i * 12]}
        for i, text in enumerate(texts)
    ]


def test_ocr_author_grid_recovers_institutions_from_independent_lines() -> None:
    texts = [
        "Ashish Vaswani*",
        "Noam Shazeer*",
        "Google Brain",
        "Google Brain",
        "Google Research",
        "avaswani@google.com",
        "noam@google.com",
        "Aidan N. Gomez*†",
        "University of Toronto",
        "aidan@cs.toronto.edu",
    ]
    authors, affiliations = extract_front_matter_people(
        [
            [
                _raw("Attention Is All You Need", 50),
                _raw(" ".join(texts), 100, _grid_lines(texts)),
                _raw("Abstract", 330),
            ]
        ],
        "Attention Is All You Need",
    )

    assert authors == ["Ashish Vaswani", "Noam Shazeer", "Aidan N. Gomez"]
    assert affiliations == ["Google Brain", "Google Research", "University of Toronto"]


def test_ocr_single_starred_author_is_kept_despite_other_email_row_alignment() -> None:
    lines = [
        {"text": "Alice Smith", "bbox": [60, 100, 160, 115]},
        {"text": "alice@example.org", "bbox": [300, 100, 430, 115]},
        {"text": "Bob Jones", "bbox": [60, 130, 160, 145]},
        {"text": "bob@example.org", "bbox": [300, 130, 430, 145]},
    ]
    last_lines = [
        {"text": "Illia Polosukhin*", "bbox": [220, 200, 350, 210]},
        {"text": "illia.polosukhin@gmail.com", "bbox": [180, 220, 410, 230]},
    ]
    authors, affiliations = extract_front_matter_people(
        [
            [
                _raw("Test Paper", 50),
                _raw("Author metadata @", 100, lines),
                _raw("Illia Polosukhin* illia.polosukhin@gmail.com", 200, last_lines),
                _raw("Abstract", 270),
            ]
        ],
        "Test Paper",
    )

    assert authors == ["Alice Smith", "Bob Jones", "Illia Polosukhin"]
    assert affiliations == []


def test_numbered_ocr_email_is_not_an_affiliation() -> None:
    texts = ["1lion@google. com", "1 Google Research", "*Work performed while at Google Brain."]
    _, affiliations = extract_front_matter_people(
        [
            [
                _raw("Test Paper", 50),
                _raw("Alice Smith", 80),
                _raw("Abstract", 120),
                _raw(" ".join(texts), 660, _grid_lines(texts)),
            ]
        ],
        "Test Paper",
    )

    assert affiliations == ["Google Research"]


def test_merged_grid_stops_before_abstract_and_body_organization_mentions() -> None:
    texts = [
        "Alice Smith*",
        "Microsoft Research",
        "alice@example.org",
        "5 Abstract",
        "Bob Jones* studies unrelated work at Google Research.",
    ]
    authors, affiliations = extract_front_matter_people(
        [
            [
                _raw("Test Paper", 50),
                _raw(" ".join(texts), 100, _grid_lines(texts)),
            ]
        ],
        "Test Paper",
    )

    assert authors == ["Alice Smith"]
    assert affiliations == ["Microsoft Research"]


def test_merged_text_without_source_lines_does_not_invent_affiliation_boundaries() -> None:
    authors, affiliations = extract_front_matter_people(
        [
            [
                _raw("Test Paper", 50),
                _raw("Alice Smith* Google Research alice@example.org", 100),
                _raw("Abstract", 160),
            ]
        ],
        "Test Paper",
    )

    assert authors == ["Alice Smith"]
    assert affiliations == []


def test_mixed_starred_and_unstarred_author_row_keeps_both_names_only() -> None:
    lines = [
        {"text": "Alice Smith*", "bbox": [60, 100, 150, 115]},
        {"text": "Bob Jones", "bbox": [200, 100, 280, 115]},
        {"text": "Google Research", "bbox": [60, 120, 200, 135]},
        {"text": "New York", "bbox": [60, 140, 150, 155]},
        {"text": "alice@example.org", "bbox": [60, 160, 200, 175]},
    ]
    authors, affiliations = extract_front_matter_people(
        [
            [
                _raw("Test Paper", 50),
                _raw(" ".join(line["text"] for line in lines), 100, lines),
                _raw("Abstract", 200),
            ]
        ],
        "Test Paper",
    )

    assert authors == ["Alice Smith", "Bob Jones"]
    assert affiliations == ["Google Research"]
