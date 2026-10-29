import pytest

from app.schemas.document import CitationMentionRead
from app.services.author_year_citations import author_year_mentions, bibliography_keys
from app.services.document_content import reference_links


def ref(label, text):
    return {"label": label, "text": text, "reference_id": f"ref-{label}",
            "page_number": 8, "block_ids": [f"r-{label}"]}


@pytest.mark.parametrize("text", [
    "Krizhevsky et al., 2012", "Krizhevsky et al. (2012)",
    "(Krizhevsky et al.,\n2012)", "Krizhevsky et al. 2012",
])
def test_author_year_forms_preserve_literal_offsets(text):
    keys = bibliography_keys([ref("author-year-1",
        "Alex Krizhevsky, Ilya Sutskever, and Geoffrey E Hinton. Imagenet. In NIPS, 2012.")])
    hits = author_year_mentions(text, keys)
    assert len(hits) == 1
    assert hits[0]["label"] == "author-year-1"
    assert text[hits[0]["start_offset"]:hits[0]["end_offset"]] == hits[0]["citation_text"]


def test_two_authors_require_both_and_support_inverted_names():
    keys = bibliography_keys([ref("x", "Kingma, Diederik P. and Jimmy Ba. Adam. 2015.")])
    assert author_year_mentions("Kingma & Ba (2015)", keys)[0]["label"] == "x"
    assert not author_year_mentions("Kingma and Smith (2015)", keys)


def test_same_surname_year_stays_unlinked_until_disambiguated():
    keys = bibliography_keys([ref("a", "John Smith and Jane Lee. Work. 2020."),
                              ref("b", "Anne Smith and Joe Wu. Other. 2020.")])
    assert not author_year_mentions("Smith et al., 2020", keys)
    assert author_year_mentions("Smith and Wu (2020)", keys)[0]["label"] == "b"


def test_year_suffix_is_not_dropped_and_shared_years_are_not_guessed():
    keys = bibliography_keys([ref("a", "John Smith. First. 2020a."),
                              ref("b", "John Smith. Second. 2020b.")])
    assert not author_year_mentions("Smith (2020)", keys)
    hits = author_year_mentions("Smith (2020a); Smith, 2020b", keys)
    assert [m["label"] for m in hits] == ["a", "b"]


def test_reference_with_multiple_dates_is_not_guessed():
    keys = bibliography_keys([ref("a", "John Smith. Work of 2019. Updated 2020.")])
    assert not author_year_mentions("Smith (2020)", keys)


def test_suffix_and_embedded_word_negatives():
    keys = bibliography_keys([ref("a", "John Smith. Work. 2020.")])
    assert not author_year_mentions("BlackSmith (2020); Smith (2020a); Smith 20201", keys)


def test_reference_blocks_excluded_and_schema_preserves_marker():
    reference = ref("author-year-1", "Alex Krizhevsky. Work. 2012.")
    body = "See Krizhevsky (2012)."
    parsed = {"references": [reference], "pages": [{"page_number": 2, "blocks": [
        {"block_id": "body", "text": body, "bbox": [1, 2, 3, 4]},
        {"block_id": "r-author-year-1", "text": body},
    ]}]}
    _, mentions = reference_links(parsed)
    assert len(mentions) == 1
    validated = CitationMentionRead(**mentions[0]).model_dump()
    assert validated["citation_text"] == "Krizhevsky (2012"
    assert validated["start_offset"] == 4
    assert validated["bbox"] == [1, 2, 3, 4]


def test_grouped_years_do_not_make_every_year_on_page_clickable():
    keys = bibliography_keys([ref("a", "John Smith. Work. 2020."),
                              ref("b", "John Smith. Other work. 2021.")])
    hits = author_year_mentions("Smith (2020, 2021)", keys)
    assert [m["citation_text"] for m in hits] == ["Smith (2020"]
