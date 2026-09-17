import csv
import hashlib
import io
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import fmean, stdev

from app.schemas.visualization import (
    AggregationMode,
    ChartRequestType,
    ChartSeriesRead,
    CsvColumnSummary,
    CsvVisualizationRead,
    ErrorBarMode,
    ScientificChartRead,
    SeriesStatisticsRead,
)

MAX_ROWS = 5000
MAX_COLUMNS = 50
PREVIEW_ROWS = 20
MAX_LINE_POINTS = 40
MAX_BAR_POINTS = 16
MAX_SERIES = 4
MAX_RADAR_METRICS = 8
MAX_RADAR_SERIES = 5
NUMBER_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m/%d/%Y", "%d/%m/%Y")
ERROR_LABELS: dict[ErrorBarMode, str] = {
    "none": "",
    "std": "样本标准差",
    "sem": "标准误",
    "ci95": "95% 置信区间（1.96 × 标准误，正态近似）",
}


class CsvVisualizationError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedCsv:
    headers: list[str]
    rows: list[list[str]]
    encoding: str
    delimiter: str
    warnings: list[str]


def _decode_csv(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise CsvVisualizationError("CSV 编码无法识别，请使用 UTF-8 或 GB18030。")


def _clean_headers(raw_headers: list[str]) -> tuple[list[str], list[str]]:
    headers: list[str] = []
    warnings: list[str] = []
    seen: dict[str, int] = {}
    for index, raw_header in enumerate(raw_headers, start=1):
        base = raw_header.strip() or f"column_{index}"
        seen[base] = seen.get(base, 0) + 1
        header = base if seen[base] == 1 else f"{base}_{seen[base]}"
        if header != raw_header.strip():
            warnings.append(f"列名“{raw_header or '(空)'}”已规范化为“{header}”。")
        headers.append(header)
    return headers, warnings


def parse_csv(content: bytes) -> ParsedCsv:
    if not content:
        raise CsvVisualizationError("CSV 文件为空。")
    text, encoding = _decode_csv(content)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        raw_headers = next(reader)
    except StopIteration as exc:
        raise CsvVisualizationError("CSV 文件缺少表头。") from exc
    if not raw_headers or not any(item.strip() for item in raw_headers):
        raise CsvVisualizationError("CSV 文件缺少有效表头。")
    if len(raw_headers) > MAX_COLUMNS:
        raise CsvVisualizationError(f"CSV 最多支持 {MAX_COLUMNS} 列。")

    headers, warnings = _clean_headers(raw_headers)
    rows: list[list[str]] = []
    extra_rows = 0
    width_warning = False
    for raw_row in reader:
        if not raw_row or not any(cell.strip() for cell in raw_row):
            continue
        if len(rows) >= MAX_ROWS:
            extra_rows += 1
            continue
        if len(raw_row) != len(headers):
            width_warning = True
        normalized = [cell.strip() for cell in raw_row[: len(headers)]]
        normalized.extend([""] * (len(headers) - len(normalized)))
        rows.append(normalized)
    if not rows:
        raise CsvVisualizationError("CSV 文件没有可分析的数据行。")
    if extra_rows:
        warnings.append(f"文件超过 {MAX_ROWS} 行，本次仅分析前 {MAX_ROWS} 行。")
    if width_warning:
        warnings.append("部分数据行与表头列数不一致，缺失单元格已保留为空。")
    return ParsedCsv(headers, rows, encoding, delimiter, warnings)


def _parse_number(value: str) -> float | None:
    candidate = value.strip()
    if not candidate:
        return None
    percent = candidate.endswith("%")
    if percent:
        candidate = candidate[:-1]
    candidate = candidate.replace(",", "").replace("¥", "").replace("$", "")
    if not NUMBER_RE.fullmatch(candidate.strip()):
        return None
    number = float(candidate)
    if not math.isfinite(number):
        return None
    return number / 100 if percent else number


def _is_date(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    try:
        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        return True
    except ValueError:
        pass
    for date_format in DATE_FORMATS:
        try:
            datetime.strptime(candidate, date_format)
            return True
        except ValueError:
            continue
    return False


def _summarize_columns(parsed: ParsedCsv) -> list[CsvColumnSummary]:
    summaries: list[CsvColumnSummary] = []
    for index, header in enumerate(parsed.headers):
        values = [row[index] for row in parsed.rows]
        present = [value for value in values if value]
        numbers = [_parse_number(value) for value in present]
        numeric_values = [value for value in numbers if value is not None]
        date_count = sum(_is_date(value) for value in present)
        if not present:
            kind = "empty"
        elif len(numeric_values) / len(present) >= 0.8:
            kind = "numeric"
        elif date_count / len(present) >= 0.8:
            kind = "date"
        else:
            kind = "categorical"
        summaries.append(
            CsvColumnSummary(
                name=header,
                kind=kind,
                missing_count=len(values) - len(present),
                distinct_count=len(set(present)),
                numeric_count=len(numeric_values),
                minimum=min(numeric_values) if numeric_values else None,
                maximum=max(numeric_values) if numeric_values else None,
                mean=(sum(numeric_values) / len(numeric_values)) if numeric_values else None,
            )
        )
    return summaries


def _sample_indices(row_count: int, limit: int) -> list[int]:
    if row_count <= limit:
        return list(range(row_count))
    if limit <= 1:
        return [0]
    return sorted({round(index * (row_count - 1) / (limit - 1)) for index in range(limit)})


def _is_monotonic_numeric_column(parsed: ParsedCsv, column_index: int) -> bool:
    values = [_parse_number(row[column_index]) for row in parsed.rows]
    if len(values) < 2 or any(value is None for value in values):
        return False
    numeric_values = [value for value in values if value is not None]
    increasing = all(
        current >= previous
        for previous, current in zip(numeric_values, numeric_values[1:], strict=False)
    )
    decreasing = all(
        current <= previous
        for previous, current in zip(numeric_values, numeric_values[1:], strict=False)
    )
    return increasing or decreasing


def _format_number(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{value:,.3g}"
    if magnitude and magnitude < 0.01:
        return f"{value:.3g}"
    return f"{value:.4g}"


def _column_index(parsed: ParsedCsv, name: str | None, role: str) -> int | None:
    if not name:
        return None
    try:
        return parsed.headers.index(name)
    except ValueError as exc:
        raise CsvVisualizationError(f"{role}列“{name}”不存在。") from exc


def _series_statistics(name: str, values: list[float]) -> SeriesStatisticsRead:
    if not values:
        return SeriesStatisticsRead(name=name, count=0)
    return SeriesStatisticsRead(
        name=name,
        count=len(values),
        mean=fmean(values),
        standard_deviation=stdev(values) if len(values) >= 2 else None,
        minimum=min(values),
        maximum=max(values),
    )


def _error_value(values: list[float], mode: ErrorBarMode) -> float | None:
    if mode == "none" or len(values) < 2:
        return None
    standard_deviation = stdev(values)
    if mode == "std":
        return standard_deviation
    standard_error = standard_deviation / math.sqrt(len(values))
    return standard_error if mode == "sem" else 1.96 * standard_error


def _raw_chart_data(
    parsed: ParsedCsv,
    *,
    x_index: int | None,
    y_indices: list[int],
    limit: int,
    warnings: list[str],
) -> tuple[list[str], list[ChartSeriesRead], list[SeriesStatisticsRead]]:
    indices = _sample_indices(len(parsed.rows), limit)
    if len(indices) < len(parsed.rows):
        warnings.append(
            f"图表从 {len(parsed.rows)} 行中等距展示 {len(indices)} 行；"
            "统计摘要仍基于全部已分析行。"
        )
    categories = [
        (parsed.rows[index][x_index] or "（空）") if x_index is not None else str(index + 1)
        for index in indices
    ]
    series: list[ChartSeriesRead] = []
    statistics: list[SeriesStatisticsRead] = []
    for column_index in y_indices:
        displayed_values = [
            _parse_number(parsed.rows[row_index][column_index]) for row_index in indices
        ]
        all_values = [
            value
            for row in parsed.rows
            if (value := _parse_number(row[column_index])) is not None
        ]
        name = parsed.headers[column_index]
        series.append(
            ChartSeriesRead(
                name=name,
                values=displayed_values,
                errors=[None] * len(displayed_values),
                sample_sizes=[1 if value is not None else 0 for value in displayed_values],
            )
        )
        statistics.append(_series_statistics(name, all_values))
    return categories, series, statistics


def _aggregated_chart_data(
    parsed: ParsedCsv,
    *,
    x_index: int,
    y_indices: list[int],
    group_index: int | None,
    error_mode: ErrorBarMode,
    limit: int,
    warnings: list[str],
) -> tuple[list[str], list[ChartSeriesRead], list[SeriesStatisticsRead]]:
    categories: list[str] = []
    category_seen: set[str] = set()
    series_names: list[str] = []
    series_seen: set[str] = set()
    buckets: dict[str, dict[str, list[float]]] = {}

    for row in parsed.rows:
        category = row[x_index] or "（空）"
        if category not in category_seen:
            category_seen.add(category)
            categories.append(category)
        group = (row[group_index] or "（空分组）") if group_index is not None else None
        for column_index in y_indices:
            metric = parsed.headers[column_index]
            name = group if group is not None and len(y_indices) == 1 else metric
            if group is not None and len(y_indices) > 1:
                name = f"{group} · {metric}"
            if name not in series_seen:
                series_seen.add(name)
                series_names.append(name)
                buckets[name] = {}
            values = buckets[name].setdefault(category, [])
            value = _parse_number(row[column_index])
            if value is not None:
                values.append(value)

    if len(series_names) > MAX_SERIES:
        warnings.append(f"分组后产生 {len(series_names)} 个序列，仅展示前 {MAX_SERIES} 个。")
        series_names = series_names[:MAX_SERIES]
    category_indices = _sample_indices(len(categories), limit)
    displayed_categories = [categories[index] for index in category_indices]
    if len(displayed_categories) < len(categories):
        warnings.append(
            f"聚合后共有 {len(categories)} 个类别，图表等距展示 {len(displayed_categories)} 个。"
        )

    series: list[ChartSeriesRead] = []
    statistics: list[SeriesStatisticsRead] = []
    insufficient_error_points = 0
    for name in series_names:
        displayed_buckets = [buckets[name].get(category, []) for category in displayed_categories]
        values = [fmean(items) if items else None for items in displayed_buckets]
        errors = [_error_value(items, error_mode) for items in displayed_buckets]
        sample_sizes = [len(items) for items in displayed_buckets]
        if error_mode != "none":
            insufficient_error_points += sum(0 < len(items) < 2 for items in displayed_buckets)
        raw_values = [value for items in buckets[name].values() for value in items]
        series.append(
            ChartSeriesRead(
                name=name,
                values=values,
                errors=errors,
                sample_sizes=sample_sizes,
            )
        )
        statistics.append(_series_statistics(name, raw_values))
    if insufficient_error_points:
        warnings.append(
            f"{insufficient_error_points} 个聚合点只有 1 个有效观测，无法计算所选误差棒。"
        )
    return displayed_categories, series, statistics


def _series_facts(
    chart_type: str,
    categories: list[str],
    series: list[ChartSeriesRead],
) -> tuple[list[str], str]:
    primary = series[0]
    points = [
        (index, value)
        for index, value in enumerate(primary.values)
        if value is not None
    ]
    if not points:
        return [], "所选主序列没有足够的有效数值。"
    minimum_index, minimum = min(points, key=lambda item: item[1])
    maximum_index, maximum = max(points, key=lambda item: item[1])
    facts = [
        f"{primary.name} 最低值 {_format_number(minimum)}（{categories[minimum_index]}）",
        f"{primary.name} 最高值 {_format_number(maximum)}（{categories[maximum_index]}）",
    ]
    if chart_type == "line" and len(points) >= 2:
        first_index, first = points[0]
        last_index, last = points[-1]
        change = last - first
        direction = "上升" if change > 0 else "下降" if change < 0 else "持平"
        if first:
            percent = change / abs(first) * 100
            trend = (
                f"{primary.name} 从 {categories[first_index]} 的 {_format_number(first)} "
                f"变为 {categories[last_index]} 的 {_format_number(last)}"
                f"（{direction} {abs(percent):.1f}%）"
            )
        else:
            trend = (
                f"{primary.name} 从 {categories[first_index]} 的 {_format_number(first)} "
                f"变为 {categories[last_index]} 的 {_format_number(last)}（{direction}）"
            )
        facts.insert(0, trend)
    summary = "；".join(facts[:3]) + "。"
    return facts, summary


def _python_literal(value: object) -> str:
    return repr(value)


def _matplotlib_script(
    chart_type: str,
    title: str,
    categories: list[str],
    series: list[ChartSeriesRead],
    x_label: str,
    y_label: str,
) -> str:
    series_data = {item.name: item.values for item in series}
    error_data = {item.name: item.errors for item in series}
    prelude = (
        "import math\n"
        "import matplotlib.pyplot as plt\n\n"
        f"categories = {_python_literal(categories)}\n"
        f"series = {_python_literal(series_data)}\n"
        f"errors = {_python_literal(error_data)}\n"
        f"title = {_python_literal(title)}\n\n"
    )
    if chart_type == "line":
        body = (
            "fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)\n"
            "x = list(range(len(categories)))\n"
            "for name, values in series.items():\n"
            "    y = [float('nan') if value is None else value for value in values]\n"
            "    raw_errors = errors.get(name, [])\n"
            "    yerr = [0.0 if value is None else value for value in raw_errors]\n"
            "    if any(value > 0 for value in yerr):\n"
            "        ax.errorbar(x, y, yerr=yerr, marker='o', linewidth=2, "
            "capsize=4, label=name)\n"
            "    else:\n"
            "        ax.plot(x, y, marker='o', linewidth=2, markersize=4, label=name)\n"
            "ax.set_xticks(x, categories, rotation=35, ha='right')\n"
        )
    elif chart_type == "bar":
        body = (
            "fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)\n"
            "x = list(range(len(categories)))\n"
            "width = 0.8 / max(len(series), 1)\n"
            "for index, (name, values) in enumerate(series.items()):\n"
            "    y = [float('nan') if value is None else value for value in values]\n"
            "    raw_errors = errors.get(name, [])\n"
            "    yerr = [0.0 if value is None else value for value in raw_errors]\n"
            "    offsets = [value + (index - (len(series) - 1) / 2) * width for value in x]\n"
            "    ax.bar(offsets, y, width=width, yerr=yerr if any(yerr) else None, "
            "capsize=4, label=name)\n"
            "ax.set_xticks(x, categories, rotation=35, ha='right')\n"
            "ax.axhline(0, color='#53645c', linewidth=0.8)\n"
        )
    else:
        body = (
            "from mpl_toolkits.mplot3d.art3d import Poly3DCollection\n"
            "fig = plt.figure(figsize=(9, 6), constrained_layout=True)\n"
            "ax = fig.add_subplot(111, projection='3d')\n"
            "angles = [2 * math.pi * i / len(categories) for i in range(len(categories))]\n"
            "colors = ['#25734f', '#d77a2b', '#3e7fa3', '#8c5aa6', '#7a8b3a']\n"
            "for level, (name, values) in enumerate(series.items()):\n"
            "    radii = [0 if value is None else value for value in values]\n"
            "    points = [(r * math.cos(a), r * math.sin(a), level) "
            "for a, r in zip(angles, radii)]\n"
            "    points.append(points[0])\n"
            "    color = colors[level % len(colors)]\n"
            "    ax.plot(*zip(*points), color=color, linewidth=2, label=name)\n"
            "    ax.add_collection3d(Poly3DCollection("
            "[points[:-1]], facecolor=color, alpha=0.16))\n"
            "for angle, label in zip(angles, categories):\n"
            "    ax.text(1.12 * math.cos(angle), 1.12 * math.sin(angle), 0, label, ha='center')\n"
            "ax.set_xlim(-1.2, 1.2); ax.set_ylim(-1.2, 1.2)\n"
            "ax.set_zlim(0, max(len(series) - 1, 1))\n"
            "ax.set_axis_off()\n"
        )
    return (
        prelude
        + body
        + f"ax.set_title({_python_literal(title)})\n"
        + f"ax.set_xlabel({_python_literal(x_label)})\n"
        + f"ax.set_ylabel({_python_literal(y_label)})\n"
        + "ax.legend(frameon=False, loc='upper left')\n"
        + "plt.savefig('scientific_figure.png', dpi=300, bbox_inches='tight')\n"
        + "plt.show()\n"
    )


def _regular_chart(
    filename: str,
    parsed: ParsedCsv,
    summaries: list[CsvColumnSummary],
    requested_type: ChartRequestType,
    warnings: list[str],
    *,
    x_column: str | None,
    y_columns: list[str] | None,
    group_column: str | None,
    aggregation: AggregationMode,
    error_mode: ErrorBarMode,
) -> ScientificChartRead:
    numeric_indices = [index for index, item in enumerate(summaries) if item.kind == "numeric"]
    if not numeric_indices:
        raise CsvVisualizationError("CSV 中没有足够稳定的数值列，无法生成图表。")

    explicit_x_index = _column_index(parsed, x_column, "X 轴")
    first_kind = summaries[0].kind
    numeric_x_axis = (
        first_kind == "numeric"
        and len(numeric_indices) > 1
        and _is_monotonic_numeric_column(parsed, 0)
    )
    x_index: int | None = explicit_x_index
    if x_index is None and (first_kind in {"date", "categorical"} or numeric_x_axis):
        x_index = 0
    if x_index is not None and summaries[x_index].kind == "empty":
        raise CsvVisualizationError("X 轴列不能是空列。")

    selected_names = list(dict.fromkeys(y_columns or []))
    if len(selected_names) > MAX_SERIES:
        raise CsvVisualizationError(f"Y 轴最多选择 {MAX_SERIES} 个数值列。")
    selected_numeric: list[int] = []
    for name in selected_names:
        column_index = _column_index(parsed, name, "Y 轴")
        if column_index is None or summaries[column_index].kind != "numeric":
            raise CsvVisualizationError(f"Y 轴列“{name}”不是稳定的数值列。")
        if column_index == x_index:
            raise CsvVisualizationError(f"列“{name}”不能同时作为 X 轴和 Y 轴。")
        selected_numeric.append(column_index)

    candidate_numeric = [index for index in numeric_indices if index != x_index]
    if not selected_numeric:
        if not candidate_numeric:
            candidate_numeric = numeric_indices
        selected_numeric = candidate_numeric[:MAX_SERIES]
    if not selected_numeric:
        raise CsvVisualizationError("没有可用的 Y 轴数值列。")

    group_index = _column_index(parsed, group_column, "分组")
    if group_index is not None:
        if group_index == x_index or group_index in selected_numeric:
            raise CsvVisualizationError("分组列不能与 X 轴或 Y 轴列重复。")
        if summaries[group_index].kind == "empty":
            raise CsvVisualizationError("分组列不能是空列。")

    if requested_type == "auto":
        x_kind = summaries[x_index].kind if x_index is not None else None
        chart_type = "line" if x_kind == "date" or (x_index == 0 and numeric_x_axis) else "bar"
    else:
        chart_type = requested_type
    limit = MAX_LINE_POINTS if chart_type == "line" else MAX_BAR_POINTS
    effective_aggregation: AggregationMode = aggregation
    if group_index is not None and effective_aggregation == "raw":
        effective_aggregation = "mean"
        warnings.append("使用分组列时自动按 X 轴类别计算均值。")
    if error_mode != "none":
        effective_aggregation = "mean"
        if x_index is None:
            raise CsvVisualizationError("误差棒需要明确的 X 轴列来聚合同类重复观测。")

    if effective_aggregation == "mean":
        if x_index is None:
            raise CsvVisualizationError("均值聚合需要选择 X 轴列。")
        categories, series, statistics = _aggregated_chart_data(
            parsed,
            x_index=x_index,
            y_indices=selected_numeric,
            group_index=group_index,
            error_mode=error_mode,
            limit=limit,
            warnings=warnings,
        )
    else:
        categories, series, statistics = _raw_chart_data(
            parsed,
            x_index=x_index,
            y_indices=selected_numeric,
            limit=limit,
            warnings=warnings,
        )

    if not selected_names and len(candidate_numeric) > len(selected_numeric):
        warnings.append(f"图表最多同时展示 {MAX_SERIES} 个数值序列。")
    missing_points = sum(value is None for item in series for value in item.values)
    if missing_points:
        warnings.append(f"图表保留 {missing_points} 个缺失或非数值点为空缺，未自动补零。")

    stem = Path(filename).stem or "CSV 数据"
    title = f"{stem}：{'趋势' if chart_type == 'line' else '组间比较'}"
    x_label = parsed.headers[x_index] if x_index is not None else "数据行"
    y_label = "、".join(item.name for item in series)
    facts, fact_summary = _series_facts(chart_type, categories, series)
    uncertainty_note = ""
    if error_mode != "none":
        sample_sizes = [
            size
            for item in series
            for size in item.sample_sizes
            if size > 0
        ]
        if sample_sizes:
            sample_range = (
                str(sample_sizes[0])
                if min(sample_sizes) == max(sample_sizes)
                else f"{min(sample_sizes)}–{max(sample_sizes)}"
            )
            uncertainty_fact = (
                f"误差棒表示{ERROR_LABELS[error_mode]}，每个聚合点包含 "
                f"{sample_range} 个有效观测"
            )
            facts.append(uncertainty_fact)
            uncertainty_note = uncertainty_fact + "；"
    aggregation_note = "重复类别按算术平均值聚合；" if effective_aggregation == "mean" else ""
    caption = (
        f"图 1. {title}。{fact_summary}"
        f"图中展示 {len(categories)} 个观测点和 {len(series)} 个数值序列；"
        f"{aggregation_note}{uncertainty_note}缺失值保留为空缺。"
    )
    return ScientificChartRead(
        chart_type=chart_type,
        title=title,
        categories=categories,
        series=series,
        x_label=x_label,
        y_label=y_label,
        caption=caption,
        caption_facts=facts,
        matplotlib_script=_matplotlib_script(
            chart_type, title, categories, series, x_label, y_label
        ),
        x_column=parsed.headers[x_index] if x_index is not None else None,
        y_columns=[parsed.headers[index] for index in selected_numeric],
        group_column=parsed.headers[group_index] if group_index is not None else None,
        aggregation=effective_aggregation,
        error_mode=error_mode,
        statistics=statistics,
    )


def _radar_chart(
    filename: str,
    parsed: ParsedCsv,
    summaries: list[CsvColumnSummary],
    warnings: list[str],
) -> ScientificChartRead:
    numeric_indices = [index for index, item in enumerate(summaries) if item.kind == "numeric"]
    if len(numeric_indices) < 3:
        raise CsvVisualizationError("三维雷达图至少需要 3 个数值列。")
    metric_indices = numeric_indices[:MAX_RADAR_METRICS]
    row_indices = list(range(min(len(parsed.rows), MAX_RADAR_SERIES)))
    if len(numeric_indices) > len(metric_indices):
        warnings.append(f"雷达图最多展示前 {MAX_RADAR_METRICS} 个数值指标。")
    if len(parsed.rows) > len(row_indices):
        warnings.append(f"雷达图最多展示前 {MAX_RADAR_SERIES} 条记录。")

    label_index = next(
        (index for index, item in enumerate(summaries) if item.kind in {"categorical", "date"}),
        None,
    )
    raw_by_metric = [
        [_parse_number(parsed.rows[row_index][column_index]) for row_index in row_indices]
        for column_index in metric_indices
    ]
    normalized_by_metric: list[list[float | None]] = []
    constant_metrics: list[str] = []
    for column_index, raw_values in zip(metric_indices, raw_by_metric, strict=True):
        present = [value for value in raw_values if value is not None]
        if not present:
            normalized_by_metric.append([None] * len(raw_values))
            continue
        low, high = min(present), max(present)
        if math.isclose(low, high):
            normalized_by_metric.append(
                [1.0 if value is not None else None for value in raw_values]
            )
            constant_metrics.append(parsed.headers[column_index])
        else:
            normalized_by_metric.append(
                [None if value is None else (value - low) / (high - low) for value in raw_values]
            )
    if constant_metrics:
        warnings.append("常量指标归一化为 1：" + "、".join(constant_metrics) + "。")

    series: list[ChartSeriesRead] = []
    for position, row_index in enumerate(row_indices):
        label = parsed.rows[row_index][label_index] if label_index is not None else ""
        series.append(
            ChartSeriesRead(
                name=label or f"记录 {row_index + 1}",
                values=[values[position] for values in normalized_by_metric],
            )
        )
    categories = [parsed.headers[index] for index in metric_indices]

    facts: list[str] = []
    for metric_position, column_index in enumerate(metric_indices[:3]):
        points = [
            (row_position, raw_by_metric[metric_position][row_position])
            for row_position in range(len(row_indices))
            if raw_by_metric[metric_position][row_position] is not None
        ]
        if points:
            best_row, best_value = max(points, key=lambda item: item[1])
            facts.append(
                f"{series[best_row].name} 在 {parsed.headers[column_index]} 上最高"
                f"（{_format_number(best_value)}）"
            )
    stem = Path(filename).stem or "CSV 数据"
    title = f"{stem}：多指标雷达比较"
    caption = (
        f"图 1. {title}。{'；'.join(facts)}。"
        "各指标在当前展示记录内按 min-max 归一化至 0–1；层高仅用于区分记录，"
        "不编码额外数值。"
    )
    normalization = "各指标按展示记录 min-max 归一化至 0–1；常量指标记为 1。"
    statistics = [
        _series_statistics(
            parsed.headers[column_index],
            [value for value in raw_values if value is not None],
        )
        for column_index, raw_values in zip(metric_indices, raw_by_metric, strict=True)
    ]
    return ScientificChartRead(
        chart_type="radar3d",
        title=title,
        categories=categories,
        series=series,
        x_label="指标",
        y_label="归一化值",
        normalization=normalization,
        caption=caption,
        caption_facts=facts,
        matplotlib_script=_matplotlib_script(
            "radar3d", title, categories, series, "指标", "归一化值"
        ),
        x_column=parsed.headers[label_index] if label_index is not None else None,
        y_columns=categories,
        statistics=statistics,
    )


def analyze_csv(
    content: bytes,
    filename: str,
    chart_type: ChartRequestType = "auto",
    *,
    x_column: str | None = None,
    y_columns: list[str] | None = None,
    group_column: str | None = None,
    aggregation: AggregationMode = "raw",
    error_mode: ErrorBarMode = "none",
) -> CsvVisualizationRead:
    parsed = parse_csv(content)
    summaries = _summarize_columns(parsed)
    warnings = list(parsed.warnings)
    if chart_type == "radar3d":
        chart = _radar_chart(filename, parsed, summaries, warnings)
    else:
        chart = _regular_chart(
            filename,
            parsed,
            summaries,
            chart_type,
            warnings,
            x_column=x_column,
            y_columns=y_columns,
            group_column=group_column,
            aggregation=aggregation,
            error_mode=error_mode,
        )
    preview_rows = [
        {
            header: (row[index] if row[index] else None)
            for index, header in enumerate(parsed.headers)
        }
        for row in parsed.rows[:PREVIEW_ROWS]
    ]
    if len(parsed.rows) > PREVIEW_ROWS:
        warnings.append(f"表格预览仅显示前 {PREVIEW_ROWS} 行。")
    return CsvVisualizationRead(
        filename=filename,
        sha256=hashlib.sha256(content).hexdigest(),
        encoding=parsed.encoding,
        delimiter=parsed.delimiter,
        row_count=len(parsed.rows),
        column_count=len(parsed.headers),
        columns=summaries,
        preview_rows=preview_rows,
        chart=chart,
        warnings=warnings,
    )
