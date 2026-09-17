"use client";

import { FormEvent, RefObject, useMemo, useRef, useState } from "react";

import {
  analyzeCsv,
  CsvAggregationMode,
  CsvChartRequestType,
  CsvErrorBarMode,
  CsvVisualizationResponse,
  ScientificChart,
} from "../lib/api";

const PALETTE = ["#25734f", "#d77a2b", "#3e7fa3", "#8c5aa6"];

function finiteValues(chart: ScientificChart): number[] {
  return chart.series.flatMap((series) => series.values.flatMap((value, index) => {
    if (value === null || !Number.isFinite(value)) return [];
    const error = series.errors[index];
    return error !== null && Number.isFinite(error)
      ? [value - error, value + error]
      : [value];
  }));
}

function chartBounds(chart: ScientificChart): { low: number; high: number } {
  const values = finiteValues(chart);
  if (!values.length) return { low: 0, high: 1 };
  let low = Math.min(...values);
  let high = Math.max(...values);
  const span = Math.max(high - low, Math.abs(high) * 0.02, 0.01);
  if (chart.chart_type === "bar") {
    low = Math.min(0, low);
    high = Math.max(0, high) + span * 0.05;
    if (low < 0) low -= span * 0.05;
  } else {
    const padding = Math.max(span * 0.08, Math.abs(high) * 0.02, 0.01);
    low -= padding;
    high += padding;
  }
  if (low === high) return { low: low - 1, high: high + 1 };
  return { low, high };
}

function formatTick(value: number): string {
  const magnitude = Math.abs(value);
  if (magnitude >= 1000) return value.toLocaleString("zh-CN", { maximumSignificantDigits: 3 });
  if (magnitude > 0 && magnitude < 0.01) return value.toExponential(1);
  return Number(value.toPrecision(3)).toString();
}

function formatStatistic(value: number | null): string {
  return value === null ? "—" : formatTick(value);
}

function ErrorBar({ centerX, error, value, yAt }: {
  centerX: number;
  error: number | null | undefined;
  value: number;
  yAt: (point: number) => number;
}) {
  if (error === null || error === undefined || !Number.isFinite(error) || error <= 0) return null;
  const upper = yAt(value + error);
  const lower = yAt(value - error);
  return (
    <g className="chart-error-bar">
      <line x1={centerX} x2={centerX} y1={upper} y2={lower} />
      <line x1={centerX - 4} x2={centerX + 4} y1={upper} y2={upper} />
      <line x1={centerX - 4} x2={centerX + 4} y1={lower} y2={lower} />
    </g>
  );
}

function CartesianChart({ chart, svgRef }: {
  chart: ScientificChart;
  svgRef: RefObject<SVGSVGElement | null>;
}) {
  const width = 760;
  const height = 390;
  const plot = { left: 62, right: 20, top: 34, bottom: 72 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const { low, high } = chartBounds(chart);
  const xAt = (index: number) => {
    if (chart.chart_type === "bar") {
      return plot.left + ((index + 0.5) / Math.max(chart.categories.length, 1)) * plotWidth;
    }
    return plot.left
      + (chart.categories.length <= 1
        ? plotWidth / 2
        : (index / (chart.categories.length - 1)) * plotWidth);
  };
  const yAt = (value: number) => plot.top + ((high - value) / (high - low)) * plotHeight;
  const ticks = Array.from({ length: 5 }, (_, index) => low + ((high - low) * index) / 4);
  const labelStep = Math.max(1, Math.ceil(chart.categories.length / 8));

  return (
    <svg
      aria-label={chart.title}
      className="scientific-chart-svg"
      ref={svgRef}
      role="img"
      viewBox={`0 0 ${width} ${height}`}
    >
      <title>{chart.title}</title>
      {ticks.map((tick) => (
        <g key={tick}>
          <line className="chart-gridline" x1={plot.left} x2={width - plot.right} y1={yAt(tick)} y2={yAt(tick)} />
          <text className="chart-tick" textAnchor="end" x={plot.left - 10} y={yAt(tick) + 4}>{formatTick(tick)}</text>
        </g>
      ))}
      <line className="chart-axis" x1={plot.left} x2={plot.left} y1={plot.top} y2={height - plot.bottom} />
      <line className="chart-axis" x1={plot.left} x2={width - plot.right} y1={yAt(Math.max(low, Math.min(high, 0)))} y2={yAt(Math.max(low, Math.min(high, 0)))} />
      {chart.categories.map((category, index) => (
        index % labelStep === 0 || index === chart.categories.length - 1 ? (
          <text
            className="chart-category"
            key={`${category}-${index}`}
            textAnchor="end"
            transform={`rotate(-34 ${xAt(index)} ${height - plot.bottom + 18})`}
            x={xAt(index)}
            y={height - plot.bottom + 18}
          >
            {category.length > 15 ? `${category.slice(0, 14)}…` : category}
          </text>
        ) : null
      ))}
      {chart.chart_type === "line" && chart.series.map((series, seriesIndex) => {
        const segments: string[] = [];
        let current: string[] = [];
        series.values.forEach((value, index) => {
          if (value === null) {
            if (current.length) segments.push(current.join(" "));
            current = [];
          } else {
            current.push(`${current.length ? "L" : "M"} ${xAt(index)} ${yAt(value)}`);
          }
        });
        if (current.length) segments.push(current.join(" "));
        return (
          <g key={series.name}>
            {segments.map((segment) => (
              <path className="chart-line" d={segment} key={segment} stroke={PALETTE[seriesIndex % PALETTE.length]} />
            ))}
            {series.values.map((value, index) => value === null ? null : (
              <g key={index}>
                <title>{series.name}：{formatTick(value)} · n={series.sample_sizes[index] ?? 0}</title>
                <ErrorBar centerX={xAt(index)} error={series.errors[index]} value={value} yAt={yAt} />
                <circle cx={xAt(index)} cy={yAt(value)} fill={PALETTE[seriesIndex % PALETTE.length]} r="3.4" />
              </g>
            ))}
          </g>
        );
      })}
      {chart.chart_type === "bar" && chart.series.flatMap((series, seriesIndex) => {
        const slot = plotWidth / Math.max(chart.categories.length, 1);
        const groupWidth = slot * 0.76;
        const barWidth = groupWidth / Math.max(chart.series.length, 1);
        const baseline = yAt(Math.max(low, Math.min(high, 0)));
        return series.values.map((value, index) => {
          if (value === null) return null;
          const y = yAt(value);
          const x = plot.left + index * slot + (slot - groupWidth) / 2 + seriesIndex * barWidth;
          const centerX = x + Math.max(barWidth - 2, 1) / 2;
          return (
            <g key={`${series.name}-${index}`}>
              <title>{series.name}：{formatTick(value)} · n={series.sample_sizes[index] ?? 0}</title>
              <rect
                fill={PALETTE[seriesIndex % PALETTE.length]}
                height={Math.max(Math.abs(baseline - y), 1)}
                rx="2"
                width={Math.max(barWidth - 2, 1)}
                x={x}
                y={Math.min(y, baseline)}
              />
              <ErrorBar centerX={centerX} error={series.errors[index]} value={value} yAt={yAt} />
            </g>
          );
        });
      })}
      <text className="chart-axis-label" textAnchor="middle" x={plot.left + plotWidth / 2} y={height - 7}>{chart.x_label}</text>
      <text className="chart-axis-label" textAnchor="middle" transform={`rotate(-90 15 ${plot.top + plotHeight / 2})`} x="15" y={plot.top + plotHeight / 2}>{chart.y_label}</text>
    </svg>
  );
}

function RadarChart({ chart, svgRef }: {
  chart: ScientificChart;
  svgRef: RefObject<SVGSVGElement | null>;
}) {
  const width = 760;
  const height = 410;
  const centerX = 360;
  const centerY = 208;
  const radius = 138;
  const point = (index: number, value: number, layer = 0) => {
    const angle = -Math.PI / 2 + (index * Math.PI * 2) / chart.categories.length;
    return [
      centerX + Math.cos(angle) * radius * value + layer * 7,
      centerY + Math.sin(angle) * radius * value - layer * 5,
    ];
  };
  const polygon = (values: Array<number | null>, layer = 0) => values
    .map((value, index) => point(index, value ?? 0, layer).join(","))
    .join(" ");

  return (
    <svg
      aria-label={chart.title}
      className="scientific-chart-svg"
      ref={svgRef}
      role="img"
      viewBox={`0 0 ${width} ${height}`}
    >
      <title>{chart.title}</title>
      {[0.25, 0.5, 0.75, 1].map((level) => (
        <polygon className="radar-grid" key={level} points={polygon(chart.categories.map(() => level))} />
      ))}
      {chart.categories.map((category, index) => {
        const [endX, endY] = point(index, 1);
        const [labelX, labelY] = point(index, 1.2);
        return (
          <g key={category}>
            <line className="radar-axis" x1={centerX} x2={endX} y1={centerY} y2={endY} />
            <text className="radar-label" textAnchor="middle" x={labelX} y={labelY}>{category.length > 13 ? `${category.slice(0, 12)}…` : category}</text>
          </g>
        );
      })}
      {[...chart.series].reverse().map((series, reverseIndex) => {
        const seriesIndex = chart.series.length - reverseIndex - 1;
        const color = PALETTE[seriesIndex % PALETTE.length];
        return (
          <polygon
            className="radar-series"
            fill={color}
            key={series.name}
            points={polygon(series.values, seriesIndex)}
            stroke={color}
          />
        );
      })}
      <text className="radar-scale-note" x="20" y="388">0–1 归一化 · 透视层高仅区分记录</text>
    </svg>
  );
}

function ChartLegend({ chart }: { chart: ScientificChart }) {
  return (
    <div className="scientific-chart-legend">
      {chart.series.map((series, index) => (
        <span key={series.name}><i style={{ background: PALETTE[index % PALETTE.length] }} />{series.name}</span>
      ))}
      {chart.error_mode !== "none" && <span className="error-legend"><i />误差棒</span>}
    </div>
  );
}

export default function DataVisualizationWorkbench() {
  const [chartType, setChartType] = useState<CsvChartRequestType>("auto");
  const [result, setResult] = useState<CsvVisualizationResponse | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [xColumn, setXColumn] = useState("");
  const [yColumns, setYColumns] = useState<string[]>([]);
  const [groupColumn, setGroupColumn] = useState("");
  const [aggregation, setAggregation] = useState<CsvAggregationMode>("raw");
  const [errorMode, setErrorMode] = useState<CsvErrorBarMode>("none");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const numericColumns = useMemo(
    () => result?.columns.filter((column) => column.kind === "numeric") || [],
    [result],
  );

  async function runAnalysis() {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const next = await analyzeCsv(file, chartType, chartType === "radar3d" ? {} : {
        xColumn: xColumn || undefined,
        yColumns,
        groupColumn: groupColumn || undefined,
        aggregation,
        errorMode,
      });
      setResult(next);
      setXColumn(next.chart.x_column ?? "");
      setYColumns(next.chart.y_columns);
      setGroupColumn(next.chart.group_column ?? "");
      setAggregation(next.chart.aggregation);
      setErrorMode(next.chart.error_mode);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "CSV 分析失败");
    } finally {
      setLoading(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void runAnalysis();
  }

  function chooseFile(nextFile: File | null) {
    setFile(nextFile);
    setResult(null);
    setError(null);
    setXColumn("");
    setYColumns([]);
    setGroupColumn("");
    setAggregation("raw");
    setErrorMode("none");
  }

  function toggleYColumn(column: string, selected: boolean) {
    setYColumns((current) => {
      if (!selected) return current.filter((item) => item !== column);
      if (current.includes(column) || current.length >= 4) return current;
      return [...current, column];
    });
  }

  async function copyScript() {
    if (!result) return;
    await navigator.clipboard.writeText(result.chart.matplotlib_script);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  function downloadSvg() {
    if (!result || !svgRef.current) return;
    const clone = svgRef.current.cloneNode(true) as SVGSVGElement;
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    const style = document.createElementNS("http://www.w3.org/2000/svg", "style");
    style.textContent = `
      .chart-gridline,.radar-grid,.radar-axis{stroke:#d5ddd7;stroke-width:1;fill:none}
      .chart-axis{stroke:#53645c;stroke-width:1.2}.chart-tick,.chart-category,.radar-label,.radar-scale-note{fill:#68776f;font:10px sans-serif}
      .chart-axis-label{fill:#38473f;font:bold 11px sans-serif}.chart-line{fill:none;stroke-width:2.2}
      .chart-error-bar line{stroke:#263a31;stroke-width:1.2}.radar-series{fill-opacity:.14;stroke-width:2}
    `;
    clone.prepend(style);
    const source = `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(clone)}`;
    const url = URL.createObjectURL(new Blob([source], { type: "image/svg+xml;charset=utf-8" }));
    const link = document.createElement("a");
    const stem = result.filename.replace(/\.(csv|tsv)$/i, "") || "scientific-chart";
    link.href = url;
    link.download = `${stem}-${result.chart.chart_type}.svg`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="data-visualization-workbench">
      <form className="csv-upload-form" onSubmit={submit}>
        <button className="csv-file-picker" onClick={() => inputRef.current?.click()} type="button">
          <strong>{file ? file.name : "选择原始 CSV"}</strong>
          <span>{file ? `${(file.size / 1024).toFixed(1)} KB` : "支持 UTF-8 / GB18030，最大 10 MB"}</span>
        </button>
        <input
          accept=".csv,.tsv,text/csv,text/tab-separated-values"
          hidden
          onChange={(event) => chooseFile(event.target.files?.[0] || null)}
          ref={inputRef}
          type="file"
        />
        <label>
          图表类型
          <select onChange={(event) => setChartType(event.target.value as CsvChartRequestType)} value={chartType}>
            <option value="auto">自动选择</option>
            <option value="line">折线图</option>
            <option value="bar">柱状图</option>
            <option value="radar3d">三维雷达图</option>
          </select>
        </label>
        <button disabled={!file || loading} type="submit">{loading ? "正在分析…" : "生成图表"}</button>
      </form>
      {error && <p className="csv-error">{error}</p>}
      {result && (
        <>
          {result.chart.chart_type !== "radar3d" && (
            <section className="csv-chart-config" aria-label="科研图表配置">
              <label>
                X 轴 / 类别
                <select
                  onChange={(event) => {
                    const next = event.target.value;
                    setXColumn(next);
                    setYColumns((columns) => columns.filter((column) => column !== next));
                    if (groupColumn === next) setGroupColumn("");
                  }}
                  value={xColumn}
                >
                  <option value="">自动识别</option>
                  {result.columns.filter((column) => column.kind !== "empty").map((column) => (
                    <option key={column.name} value={column.name}>{column.name} · {column.kind}</option>
                  ))}
                </select>
              </label>
              <fieldset>
                <legend>Y 轴指标（最多 4 个）</legend>
                <div>
                  {numericColumns.filter((column) => column.name !== xColumn && column.name !== groupColumn).map((column) => (
                    <label key={column.name}>
                      <input
                        checked={yColumns.includes(column.name)}
                        disabled={!yColumns.includes(column.name) && yColumns.length >= 4}
                        onChange={(event) => toggleYColumn(column.name, event.target.checked)}
                        type="checkbox"
                      />
                      {column.name}
                    </label>
                  ))}
                </div>
                {!yColumns.length && <small>未勾选时自动选择数值列。</small>}
              </fieldset>
              <label>
                分组列
                <select
                  onChange={(event) => {
                    const next = event.target.value;
                    setGroupColumn(next);
                    setYColumns((columns) => columns.filter((column) => column !== next));
                    if (next) setAggregation("mean");
                  }}
                  value={groupColumn}
                >
                  <option value="">不分组</option>
                  {result.columns.filter((column) => (
                    column.kind !== "empty"
                    && column.name !== xColumn
                    && !yColumns.includes(column.name)
                  )).map((column) => (
                    <option key={column.name} value={column.name}>{column.name}</option>
                  ))}
                </select>
              </label>
              <label>
                聚合方式
                <select
                  onChange={(event) => setAggregation(event.target.value as CsvAggregationMode)}
                  value={aggregation}
                >
                  <option value="raw">保留原始行</option>
                  <option value="mean">按 X 类别取均值</option>
                </select>
              </label>
              <label>
                误差棒
                <select
                  onChange={(event) => {
                    const next = event.target.value as CsvErrorBarMode;
                    setErrorMode(next);
                    if (next !== "none") setAggregation("mean");
                  }}
                  value={errorMode}
                >
                  <option value="none">不显示</option>
                  <option value="std">样本标准差</option>
                  <option value="sem">标准误 SEM</option>
                  <option value="ci95">95% CI（正态近似）</option>
                </select>
              </label>
              <button disabled={loading || !file} onClick={() => void runAnalysis()} type="button">
                {loading ? "重新计算中…" : "应用配置"}
              </button>
            </section>
          )}
          {result.chart.chart_type === "radar3d" && (
            <p className="csv-chart-mode-note">雷达图按记录比较多个数值指标，不启用分组聚合和误差棒。</p>
          )}
          {result.warnings.map((warning) => <p className="citation-warning" key={warning}>{warning}</p>)}
          <div className="csv-summary-strip">
            <span><strong>{result.row_count}</strong> 行</span>
            <span><strong>{result.column_count}</strong> 列</span>
            <span><strong>{numericColumns.length}</strong> 个数值字段</span>
            <span>{result.encoding} · {result.delimiter === "\t" ? "Tab" : result.delimiter} 分隔</span>
            <span>{result.chart.aggregation === "mean" ? "均值聚合" : "原始观测"}</span>
          </div>
          <div className="visualization-result-grid">
            <article className="scientific-figure-card">
              <header>
                <div><span>{result.chart.chart_type.toUpperCase()}</span><h3>{result.chart.title}</h3></div>
                <div className="scientific-figure-actions">
                  <button onClick={() => void copyScript()} type="button">{copied ? "已复制" : "复制 Matplotlib"}</button>
                  <button onClick={downloadSvg} type="button">下载 SVG</button>
                </div>
              </header>
              {result.chart.chart_type === "radar3d"
                ? <RadarChart chart={result.chart} svgRef={svgRef} />
                : <CartesianChart chart={result.chart} svgRef={svgRef} />}
              <ChartLegend chart={result.chart} />
              <p className="figure-caption">{result.chart.caption}</p>
              {result.chart.normalization && <small className="normalization-note">{result.chart.normalization}</small>}
            </article>
            <aside className="csv-data-panel">
              <h3>数据预览</h3>
              <div className="csv-table-scroll">
                <table>
                  <thead><tr>{result.columns.map((column) => <th key={column.name}>{column.name}</th>)}</tr></thead>
                  <tbody>
                    {result.preview_rows.map((row, rowIndex) => (
                      <tr key={rowIndex}>{result.columns.map((column) => <td key={column.name}>{row[column.name] ?? "—"}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <h3>统计摘要（有效观测）</h3>
              <div className="csv-table-scroll csv-statistics-table">
                <table>
                  <thead><tr><th>序列</th><th>n</th><th>均值</th><th>SD</th><th>最小</th><th>最大</th></tr></thead>
                  <tbody>
                    {result.chart.statistics.map((statistic) => (
                      <tr key={statistic.name}>
                        <td>{statistic.name}</td>
                        <td>{statistic.count}</td>
                        <td>{formatStatistic(statistic.mean)}</td>
                        <td>{formatStatistic(statistic.standard_deviation)}</td>
                        <td>{formatStatistic(statistic.minimum)}</td>
                        <td>{formatStatistic(statistic.maximum)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <h3>字段审计</h3>
              <div className="csv-column-audit">
                {result.columns.map((column) => (
                  <div key={column.name}>
                    <strong>{column.name}</strong><span>{column.kind}</span>
                    <small>缺失 {column.missing_count} · 去重 {column.distinct_count}</small>
                    {column.mean !== null && <small>均值 {formatTick(column.mean)}</small>}
                  </div>
                ))}
              </div>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
