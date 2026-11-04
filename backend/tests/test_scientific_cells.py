from types import SimpleNamespace

import fitz
import pytest

from app.parsers import scientific_cells as reader
from app.parsers.layout import TableSnapshot


def test_baseline_digits_and_negative_superscripts_are_not_unsigned_powers():
    boxes = [fitz.IRect(*b) for b in [(0, 10, 8, 30), (15, 19, 18, 22),
                                       (25, 10, 31, 30), (34, 10, 44, 30)]]
    raised = fitz.IRect(45, 6, 52, 19)
    assert reader._parts([*boxes, raised])
    assert reader._parts([*boxes, fitz.IRect(45, 10, 53, 30)]) is None
    assert reader._parts([*boxes, fitz.IRect(45, 13, 49, 15), raised]) is None
    assert reader._parts([boxes[0], fitz.IRect(15, 27, 18, 30), *boxes[2:], raised]) is None


@pytest.mark.parametrize(("reads", "accepted"), [
    (["4.2·10^{13}", None, "4.2·10^{13}"], True),
    (["4.2·10^{13}", "4.2·10^{12}", "4.2·10^{13}"], False),
    ([None, "4.2·10^{13}", None], False),
])
def test_disagreement_or_one_read_preserves_original(monkeypatch, reads, accepted):
    iterator = iter(reads)
    monkeypatch.setattr(reader, "_read_at_scale", lambda *args: next(iterator))
    box = [5, 5, 53, 15]
    table = TableSnapshot(box, [["4.2-10?"]], [box], 1)
    edits = reader.refine_scientific_cells(SimpleNamespace(number=2), [table], "eng", None)
    if accepted:
        assert edits[0]["original"] == "4.2-10?"
        assert edits[0]["bbox"] == box and edits[0]["page"] == 3
        assert table.rows == [["4.2·10^{13}"]]
    else:
        assert edits == [] and table.rows == [["4.2-10?"]]
