import fitz

from app.parsers.layout import TableSnapshot
from app.parsers.ocr_parser import SelectiveOcrParser


def test_only_single_row_geometry_can_supply_a_missing_cell():
    candidate = TableSnapshot([0, 0, 100, 40], [["A", "", "9"], ["B", "", "8"]],
                              [[1, 2, 18, 10], None, [79, 2, 90, 10],
                               [1, 14, 18, 22], None, [79, 14, 90, 22]], 3)
    ruled = TableSnapshot(candidate.bbox, [["", "", ""], ["", "", ""]],
                          [None, [20, 0, 70, 12], None, None, [20, 12, 70, 40], None], 3)
    SelectiveOcrParser._locate_empty_cells(candidate, ruled)
    assert candidate.cells[1] == [20, 0, 70, 12]
    assert candidate.cells[4] is None  # Spans more than one line.
    assert candidate.rows[0][1] == ""  # Only OCR of the actual crop may fill it.


def test_decimal_recovery_requires_matching_digits_at_same_location(monkeypatch):
    original = [(0, 0, 10, 8, "529"), (20, 0, 32, 8, "253"), (0, 20, 10, 28, "500")]
    alternate = [(0, 0, 10, 8, "5.29"), (20, 0, 32, 8, "25.8"), (0, 40, 10, 48, "5.00")]
    parser = object.__new__(SelectiveOcrParser)
    monkeypatch.setattr(parser, "_band_words",
                        lambda page, rect, rules, dpi: original if dpi == 300 else alternate)
    result = parser._read_table_band(None, fitz.Rect(0, 0, 50, 50), [])
    assert [word[4] for word in result] == ["5.29", "253", "500"]
