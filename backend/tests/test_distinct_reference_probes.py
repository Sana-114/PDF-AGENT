from app.parsers.base import Block, Page, ParsedDocument, ReferenceNode
from scripts.evaluate_pdf_corpus import _validate_structure


def test_distinct_reference_gold_detects_merged_anonymous_entries():
    text = "Cwe-327. First item. 2006. Cwe-780. Second item. 2009."
    box = [60, 90, 500, 120]
    parsed = ParsedDocument(schema_version="0.2.0", title="Example", authors=[],
        full_text=text, pages=[Page(1, 600, 800, [Block("p1-b1", "text", text, box, 0)])],
        references=[ReferenceNode("r1", "author-year-1", text, 1, box, ["p1-b1"])])
    gold = {"reference_samples_distinct": True, "reference_samples": [
        {"page_number": 1, "contains": "Cwe-327"},
        {"page_number": 1, "contains": "Cwe-780"},
    ]}
    assert "merged into one entry" in " ".join(_validate_structure(parsed, gold))
    parsed.references = [ReferenceNode("r1", "author-year-1", "Cwe-327. First item. 2006.",
                                      1, box, ["p1-b1"]),
                         ReferenceNode("r2", "author-year-2", "Cwe-780. Second item. 2009.",
                                      1, box, ["p1-b1"])]
    assert _validate_structure(parsed, gold) == []
