from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.config import settings
from app.schemas.visualization import (
    AggregationMode,
    ChartRequestType,
    CsvVisualizationRead,
    ErrorBarMode,
)
from app.services.csv_visualization import CsvVisualizationError, analyze_csv

router = APIRouter()


@router.post("/analyze", response_model=CsvVisualizationRead)
async def analyze_csv_upload(
    file: Annotated[UploadFile, File()],
    chart_type: Annotated[ChartRequestType, Form()] = "auto",
    x_column: Annotated[str | None, Form()] = None,
    y_columns: Annotated[list[str] | None, Form()] = None,
    group_column: Annotated[str | None, Form()] = None,
    aggregation: Annotated[AggregationMode, Form()] = "raw",
    error_mode: Annotated[ErrorBarMode, Form()] = "none",
) -> CsvVisualizationRead:
    filename = file.filename or "dataset.csv"
    if not filename.lower().endswith((".csv", ".tsv")):
        raise HTTPException(status_code=415, detail="仅支持 .csv 或 .tsv 文件。")
    content = await file.read(settings.max_csv_bytes + 1)
    if len(content) > settings.max_csv_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"CSV 文件不能超过 {settings.max_csv_mb} MB。",
        )
    try:
        return analyze_csv(
            content,
            filename,
            chart_type,
            x_column=x_column,
            y_columns=y_columns,
            group_column=group_column,
            aggregation=aggregation,
            error_mode=error_mode,
        )
    except CsvVisualizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
