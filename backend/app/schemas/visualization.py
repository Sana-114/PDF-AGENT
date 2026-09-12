from typing import Literal

from pydantic import BaseModel, Field

ChartRequestType = Literal["auto", "line", "bar", "radar3d"]
ChartType = Literal["line", "bar", "radar3d"]
ColumnKind = Literal["numeric", "date", "categorical", "empty"]


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
