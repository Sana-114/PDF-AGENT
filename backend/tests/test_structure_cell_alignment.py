from app.parsers.layout import extract_text_blocks, formula_latex_candidate
from app.parsers.text_table import PositionedWord, _snapshot_from_words


def test_empty_metric_and_fragmented_cost_preserve_the_metric_column():
    words = []
    for y, cells in [(10, [(0, "Name"), (100, "DE"), (160, "FR"), (220, "Cost")]),
                     (30, [(0, "First"), (100, "25.3"), (160, "38.0"), (220, "100")]),
                     (50, [(0, "Next"), (160, "40.4"), (220, "8.0"), (242, "10^20")])]:
        words.extend(PositionedWord([x, y, x + 12, y + 10], value) for x, value in cells)
    grid = _snapshot_from_words(words)
    assert grid is not None
    assert grid.rows[-1] == ["Next", "", "40.4", "8.0 10^20"]
    assert grid.cells[-3] is None
    assert grid.cells[-2] == [160, 50, 172, 60]


def test_formula_candidate_uses_script_baseline_without_altering_source_text():
    def span(text, x, y, size):
        return {"text": text, "origin": (x, y), "size": size, "bbox": [x, y-size, x+4, y+1]}

    spans = [span("y=", 1, 20, 10), span("ϕ", 5, 20, 10), span("0", 9, 21.5, 7),
             span("+", 13, 20, 10), span("x", 17, 20, 10), span("2", 21, 17, 7),
             span("+", 25, 20, 10), span("z", 29, 20, 10), span("3", 33, 20, 7)]
    block = extract_text_blocks({"blocks": [{"type": 0, "bbox": [1, 5, 38, 23],
                                             "lines": [{"spans": spans}]}]}, 32)[0]
    assert block.text == "y=ϕ0+x2+z3"
    assert block.formula_source is not None
    assert formula_latex_candidate(block.formula_source) == r"y= \phi_{0}+x^{2}+z3"
