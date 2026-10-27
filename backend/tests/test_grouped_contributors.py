from dataclasses import asdict

from app.parsers.base import Block, Page
from app.parsers.contributors import extract_contributor_groups
from app.parsers.layout import RawTextBlock
from app.services.author_evidence import (
    CONTRIBUTOR_SECTION,
    author_evidence_nodes,
    contributor_evidence_nodes,
)
from app.services.chunking import build_chunk_drafts


def layout(pages):
    raw_pages, built = [], []
    for number, texts in enumerate(pages, 1):
        raws, blocks = [], []
        for index, text in enumerate(texts, 1):
            box = [40, 50 + 45 * index, 550, 80 + 45 * index]
            original = " ".join(text.split())
            raws.append(
                RawTextBlock(
                    page_number=number,
                    bbox=box,
                    text=original,
                    font_size=10,
                    line_count=len(text.splitlines()),
                    char_weight=len(original),
                    lines=[{"text": line} for line in text.splitlines()],
                )
            )
            blocks.append(Block(f"p{number}-b{index}", "text", original, box, index))
        raw_pages.append(raws)
        built.append(Page(number, 600, 850, blocks))
    return raw_pages, built


def sample():
    return layout(
        [
            [
                "Example Research",
                "OpenScience Team*",
                "Key Contributors\nAlice One, Bob Two,\nCarlos",
            ],
            [
                "OpenScience Team",
                "Muñoz Ferrandis, Diana Four",
                "Data Curation\nAlice One, Evan Five",
                "Abstract",
                "We compare many methods, models, baselines.",
                "Contributors\nInvented Person, Wrong Name",
            ],
        ]
    )


def test_collective_authors_and_cross_page_contributors_are_distinct():
    raw, pages = sample()
    collective, groups = extract_contributor_groups(raw, pages, "Example Research")
    assert collective == "OpenScience Team"
    assert [group.role for group in groups] == ["Key Contributors", "Data Curation"]
    assert groups[0].members == ["Alice One", "Bob Two", "Carlos Muñoz Ferrandis", "Diana Four"]
    assert groups[1].members == ["Alice One", "Evan Five"]
    assert [source.page_number for source in groups[0].sources] == [1, 2]
    assert not any("Invented" in name for group in groups for name in group.members)


def test_group_evidence_keeps_original_per_page_anchors_not_global_ordinals():
    raw, pages = sample()
    collective, groups = extract_contributor_groups(raw, pages, "Example Research")
    parsed = {
        "authors": [collective],
        "pages": [asdict(page) for page in pages],
        "contributor_groups": [asdict(group) for group in groups],
    }
    assert "1. OpenScience Team" in author_evidence_nodes(parsed)[0]["text"]
    nodes = contributor_evidence_nodes(parsed)
    assert len(nodes) == 3
    assert nodes[0]["block_ids"] == ["p1-b3"]
    assert nodes[1]["block_ids"] == ["p2-b2"]
    assert "Carlos Muñoz Ferrandis" not in nodes[0]["text"]
    assert all("不是全局作者顺序" in node["text"] for node in nodes)
    drafts = build_chunk_drafts(parsed)
    assert sum((draft.section or "").startswith(CONTRIBUTOR_SECTION) for draft in drafts) == 3
    parsed["contributor_groups"][0]["sources"][1]["text"] = "Forged Name"
    assert len(contributor_evidence_nodes(parsed)) == 1  # whole first group excluded


def test_ordinary_author_rows_and_body_contribution_discussions_do_not_activate():
    raw, pages = layout(
        [
            [
                "Example Research",
                "Alice One, Bob Two",
                "Abstract",
                "OpenScience Team",
                "Contributors\nWrong Person, Wrong Name",
            ]
        ]
    )
    assert extract_contributor_groups(raw, pages, "Example Research") == (None, [])
    raw, pages = layout(
        [
            [
                "Example Research",
                "OpenScience Team",
                "Abstract",
                "Contributors\nWrong Person, Wrong Name",
            ]
        ]
    )
    assert extract_contributor_groups(raw, pages, "Example Research") == (None, [])


def test_line_wrapping_preserves_initials_hyphens_and_literal_accents():
    raw, pages = layout(
        [
            [
                "Example Research",
                "OpenScience Team",
                "Contributors\nAlexander M.\nRush, Er-\nin Six, Anne-\nMarie Seven, Suzana Ili´c",
                "Abstract",
            ]
        ]
    )
    _, groups = extract_contributor_groups(raw, pages, "Example Research")
    assert groups[0].members == [
        "Alexander M. Rush",
        "Erin Six",
        "Anne-Marie Seven",
        "Suzana Ili´c",
    ]


def test_unverified_geometry_and_page_limited_prefix_do_not_claim_complete_groups():
    raw, pages = sample()
    pages[0].blocks[2].text = "Mismatched source"
    assert extract_contributor_groups(raw, pages, "Example Research") == (None, [])
    raw, pages = layout(
        [
            ["Example Research", "OpenScience Team", "Contributors\nAlice One, Bob Two"],
            [],
            [],
            ["Abstract"],
        ]
    )
    assert extract_contributor_groups(raw, pages, "Example Research") == (None, [])


def test_legacy_ast_has_no_invented_contribution_groups():
    assert contributor_evidence_nodes({"authors": ["Alice One"], "pages": []}) == []
