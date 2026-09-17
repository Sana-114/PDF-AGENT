from typing import Literal

from pydantic import BaseModel, Field

ChartRequestType = Literal["auto", "line", "bar", "radar3d"]
ChartType = Literal["line", "bar", "radar3d"]
ColumnKind = Literal["numeric", "date", "categorical", "empty"]
AggregationMode = Literal["raw", "mean"]
ErrorBarMode = Literal["none", "std", "sem", "ci95"]


class CsvColumnSummary(BaseModel):
    name: str
    kind: ColumnKind
    missing_count: int
    distinct_count: int
    numeric_count: int
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None


class ChartSeriesRead(BaseModel):
    name: str
    values: list[float | None]
    errors: list[float | None] = Field(default_factory=list)
    sample_sizes: list[int] = Field(default_factory=list)


class SeriesStatisticsRead(BaseModel):
    name: str
    count: int
    mean: float | None = None
    standard_deviation: float | None = None
    minimum: float | None = None
    maximum: float | None = None


class ScientificChartRead(BaseModel):
    chart_type: ChartType
    title: str
    categories: list[str]
    series: list[ChartSeriesRead]
    x_label: str
    y_label: str
    normalization: str | None = None
    caption: str
    caption_facts: list[str] = Field(default_factory=list)
    matplotlib_script: str
    x_column: str | None = None
    y_columns: list[str] = Field(default_factory=list)
    group_column: str | None = None
    aggregation: AggregationMode = "raw"
    error_mode: ErrorBarMode = "none"
    statistics: list[SeriesStatisticsRead] = Field(default_factory=list)


class CsvVisualizationRead(BaseModel):
    filename: str
    sha256: str
    encoding: str
    delimiter: str
    row_count: int
    column_count: int
    columns: list[CsvColumnSummary]
    preview_rows: list[dict[str, str | None]]
    chart: ScientificChartRead
    warnings: list[str] = Field(default_factory=list)
