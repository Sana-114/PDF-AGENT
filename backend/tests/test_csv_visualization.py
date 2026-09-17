import math

import pytest
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


def test_grouped_mean_chart_computes_sample_standard_deviation() -> None:
    content = (
        b"dataset,model,seed,accuracy\n"
        b"D1,A,1,0.80\n"
        b"D1,A,2,0.84\n"
        b"D1,B,1,0.76\n"
        b"D1,B,2,0.80\n"
        b"D2,A,1,0.86\n"
        b"D2,A,2,0.90\n"
        b"D2,B,1,0.82\n"
        b"D2,B,2,0.84\n"
    )

    result = analyze_csv(
        content,
        "replicates.csv",
        "bar",
        x_column="dataset",
        y_columns=["accuracy"],
        group_column="model",
        aggregation="mean",
        error_mode="std",
    )

    assert result.chart.categories == ["D1", "D2"]
    assert [series.name for series in result.chart.series] == ["A", "B"]
    assert result.chart.series[0].values == pytest.approx([0.82, 0.88])
    expected_standard_deviation = math.sqrt(0.0008)
    assert result.chart.series[0].errors == pytest.approx(
        [expected_standard_deviation, expected_standard_deviation]
    )
    assert result.chart.series[0].sample_sizes == [2, 2]
    assert result.chart.statistics[0].count == 4
    assert result.chart.statistics[0].mean == pytest.approx(0.85)
    assert result.chart.error_mode == "std"
    assert "每个聚合点包含 2 个有效观测" in result.chart.caption
    assert "yerr=" in result.chart.matplotlib_script


def test_explicit_axis_selection_limits_plotted_metrics() -> None:
    content = b"epoch,accuracy,loss\n1,0.7,1.2\n2,0.8,0.8\n3,0.9,0.5\n"

    result = analyze_csv(
        content,
        "training.csv",
        "line",
        x_column="epoch",
        y_columns=["loss"],
    )

    assert result.chart.x_column == "epoch"
    assert result.chart.y_columns == ["loss"]
    assert [series.name for series in result.chart.series] == ["loss"]
    assert result.chart.series[0].values == [1.2, 0.8, 0.5]


def test_error_bars_require_an_x_axis_for_replicate_aggregation() -> None:
    with pytest.raises(CsvVisualizationError, match="X 轴"):
        analyze_csv(
            b"accuracy\n0.8\n0.9\n",
            "replicates.csv",
            "bar",
            y_columns=["accuracy"],
            error_mode="sem",
        )


def test_error_bars_warn_instead_of_inventing_uncertainty_for_single_observation() -> None:
    result = analyze_csv(
        b"dataset,score\nD1,1\nD2,2\nD2,4\n",
        "small-sample.csv",
        "bar",
        x_column="dataset",
        y_columns=["score"],
        aggregation="mean",
        error_mode="sem",
    )

    assert result.chart.series[0].values == [1.0, 3.0]
    assert result.chart.series[0].errors[0] is None
    assert result.chart.series[0].errors[1] == pytest.approx(1.0)
    assert result.chart.series[0].sample_sizes == [1, 2]
    assert any("只有 1 个有效观测" in warning for warning in result.warnings)


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


def test_visualization_api_accepts_academic_chart_configuration() -> None:
    content = b"dataset,model,score\nD1,A,1\nD1,A,3\nD1,B,2\nD1,B,4\n"
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/visualizations/analyze",
            files={"file": ("metrics.csv", content, "text/csv")},
            data={
                "chart_type": "bar",
                "x_column": "dataset",
                "y_columns": "score",
                "group_column": "model",
                "aggregation": "mean",
                "error_mode": "ci95",
            },
        )

    assert response.status_code == 200
    chart = response.json()["chart"]
    assert chart["x_column"] == "dataset"
    assert chart["y_columns"] == ["score"]
    assert chart["group_column"] == "model"
    assert chart["aggregation"] == "mean"
    assert chart["error_mode"] == "ci95"
    assert chart["series"][0]["sample_sizes"] == [2]
    assert chart["series"][0]["errors"] == pytest.approx([1.96])


def test_visualization_api_rejects_non_csv_extension() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/visualizations/analyze",
            files={"file": ("metrics.json", b"{}", "application/json")},
            data={"chart_type": "auto"},
        )

    assert response.status_code == 415
