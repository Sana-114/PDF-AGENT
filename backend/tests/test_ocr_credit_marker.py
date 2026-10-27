import pytest

from app.parsers.people import _credit, _unmarked_row_names


@pytest.mark.parametrize("marker", ["*|", "∗|", "†|", "*｜"])
def test_ocr_bar_attached_to_footnote_is_not_part_of_name(marker):
    text = "Alice Example" + marker
    assert _credit(text) == "Alice Example"
    assert text.endswith(marker)  # cleanup does not mutate source evidence


def test_marker_cluster_separates_author_rows():
    assert _unmarked_row_names("Alice Example*| Bob Sample*") == [
        "Alice Example", "Bob Sample"]


@pytest.mark.parametrize("text", ["Alice | Example", "Alice Example|", "Alice|Example"])
def test_bare_or_internal_bar_is_not_silently_rewritten(text):
    assert _credit(text) == text
