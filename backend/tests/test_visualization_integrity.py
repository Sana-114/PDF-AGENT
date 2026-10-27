import ast

from app.services.csv_visualization import analyze_csv


def test_radar_duplicate_labels_survive_export_and_missing_values_remain_gaps():
    result = analyze_csv(
        b"model,accuracy,latency_ms,memory_MB\nA,0.8,10,100\nA,,20,100\nC,0.9,15,100\n",
        "boundary.csv",
        "radar3d",
    )
    assert len({s.name for s in result.chart.series}) == 3
    assert result.chart.series[1].values[0] is None
    assert result.chart.series[1].values[2] == 1
    tree = ast.parse(result.chart.matplotlib_script)
    exported = next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "series"
    )
    assert len(exported) == 3
    assert list(exported.values())[1][0] is None
    assert "0 if value is None else value" not in result.chart.matplotlib_script
    assert "if all(value is not None" in result.chart.matplotlib_script
    assert "_radar_grid" in result.chart.matplotlib_script
    assert "_radar_spoke" in result.chart.matplotlib_script
    assert "层高仅区分记录" in result.chart.matplotlib_script
    assert "面积大小不代表综合性能" in result.chart.matplotlib_script
    assert any("不补零" in w for w in result.warnings)


def test_date_line_sorts_pairs_but_does_not_rewrite_original_preview():
    result = analyze_csv(
        b"date,score\n2026-03-01,3\n2026-01-01,1\n2026-02-01,2\n", "unsorted.csv", "line"
    )
    assert result.chart.categories == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert result.chart.series[0].values == [1, 2, 3]
    assert result.chart.x_coordinates[1] - result.chart.x_coordinates[0] == 31 * 86400
    assert result.preview_rows[0]["date"] == "2026-03-01"
    assert any("按时间排序" in w for w in result.warnings)


def test_date_line_sort_compares_timezone_offsets_as_actual_times():
    result = analyze_csv(
        b"date,score\n2026-01-01T01:00:00+02:00,1\n2025-12-31T23:30:00Z,2\n",
        "offsets.csv",
        "line",
    )
    assert result.chart.series[0].values == [1, 2]


def test_date_line_with_missing_date_does_not_silently_drop_observation():
    result = analyze_csv(b"date,score\n2026-03-01,3\n,4\n2026-01-01,1\n", "missing.csv", "line")
    assert result.chart.series[0].values == [3, 4, 1]
    assert any("缺失或无法识别" in w for w in result.warnings)


def test_numeric_line_preserves_actual_spacing_instead_of_ordinal_positions():
    result = analyze_csv(b"layers,bleu\n2,23.7\n4,25.3\n8,25.5\n", "layers.csv", "line")
    assert result.chart.x_coordinates == [2, 4, 8]
    assert "x = [2.0, 4.0, 8.0]" in result.chart.matplotlib_script
