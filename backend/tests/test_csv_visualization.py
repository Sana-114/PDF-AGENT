from fastapi.testclient import TestClient

from app.main import app
from app.services.csv_visualization import CsvVisualizationError, analyze_csv, parse_csv


def test_date_csv_generates_line_chart_and_grounded_caption() -> None:
    content = (
        b"date,accuracy,loss\n"
        b"2026-01-01,0.72,1.2\n"
        b"2026-02-01,0.81,0.8\n"
        b"2026-03-01,0.90,0.5\n"
    )

    result = analyze_csv(content, "experiment.csv")

    assert result.row_count == 3
    assert [column.kind for column in result.columns] == ["date", "numeric", "numeric"]
    assert result.chart.chart_type == "line"
    assert result.chart.categories == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert result.chart.series[0].values == [0.72, 0.81, 0.9]
    assert "上升 25.0%" in result.chart.caption
    assert "最高值 0.9" in result.chart.caption
    assert "plt.savefig('scientific_figure.png', dpi=300" in result.chart.matplotlib_script


def test_bar_chart_keeps_missing_values_as_gaps() -> None:
    content = b"model,score\nA,88\nB,\nC,92\n"

    result = analyze_csv(content, "benchmark.csv", "bar")

    assert result.chart.chart_type == "bar"
    assert result.chart.series[0].values == [88.0, None, 92.0]
    assert any("未自动补零" in warning for warning in result.warnings)
    assert "value is None" in result.chart.matplotlib_script


def test_radar_chart_normalizes_metrics_and_uses_raw_caption_facts() -> None:
    content = (
        b"model,accuracy,robustness,speed\n"
        b"A,0.91,0.72,120\n"
        b"B,0.88,0.85,150\n"
        b"C,0.90,0.80,130\n"
    )

    result = analyze_csv(content, "radar.csv", "radar3d")

    assert result.chart.chart_type == "radar3d"
    assert result.chart.categories == ["accuracy", "robustness", "speed"]
    assert [series.name for series in result.chart.series] == ["A", "B", "C"]
    assert result.chart.series[0].values == [1.0, 0.0, 0.0]
    assert "A 在 accuracy 上最高（0.91）" in result.chart.caption
    assert "B 在 speed 上最高（150）" in result.chart.caption
    assert "层高仅用于区分记录" in result.chart.caption


def test_parser_normalizes_duplicate_and_blank_headers() -> None:
    parsed = parse_csv(b"metric,metric,,value\nA,B,C,1\n")

    assert parsed.headers == ["metric", "metric_2", "column_3", "value"]
    assert len(parsed.warnings) == 2


def test_csv_without_numeric_columns_is_rejected() -> None:
    try:
        analyze_csv(b"name,status\nA,good\nB,bad\n", "labels.csv")
    except CsvVisualizationError as exc:
        assert "数值列" in str(exc)
    else:
        raise AssertionError("expected CsvVisualizationError")


def test_visualization_api_accepts_multipart_csv() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/visualizations/analyze",
            files={"file": ("metrics.csv", b"epoch,loss\n1,0.8\n2,0.4\n", "text/csv")},
            data={"chart_type": "line"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["filename"] == "metrics.csv"
    assert payload["chart"]["chart_type"] == "line"
    assert payload["chart"]["x_label"] == "epoch"
    assert payload["chart"]["series"][0]["name"] == "loss"
    assert not any("最多同时展示" in warning for warning in payload["warnings"])
    assert payload["sha256"]


def test_visualization_api_rejects_non_csv_extension() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/visualizations/analyze",
            files={"file": ("metrics.json", b"{}", "application/json")},
            data={"chart_type": "auto"},
        )

    assert response.status_code == 415
