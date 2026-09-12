"use client";

import { FormEvent, useMemo, useRef, useState } from "react";

import {
  analyzeCsv,
  CsvChartRequestType,
  CsvVisualizationResponse,
  ScientificChart,
} from "../lib/api";

const PALETTE = ["#25734f", "#d77a2b", "#3e7fa3", "#8c5aa6"];

function finiteValues(chart: ScientificChart): number[] {
  return chart.series.flatMap((item) => item.values).filter(
    (value): value is number => value !== null && Number.isFinite(value),
  );
}

function chartBounds(chart: ScientificChart): { low: number; high: number } {
  const values = finiteValues(chart);
  if (!values.length) return { low: 0, high: 1 };
  let low = Math.min(...values);
  let high = Math.max(...values);
  if (chart.chart_type === "bar") {
    low = Math.min(0, low);
    high = Math.max(0, high);
  } else {
    const padding = Math.max((high - low) * 0.08, Math.abs(high) * 0.02, 0.01);
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

function CartesianChart({ chart }: { chart: ScientificChart }) {
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
    <svg aria-label={chart.title} className="scientific-chart-svg" role="img" viewBox={`0 0 ${width} ${height}`}>
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
              <circle cx={xAt(index)} cy={yAt(value)} fill={PALETTE[seriesIndex % PALETTE.length]} key={index} r="3.4" />
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
          return (
            <rect
              fill={PALETTE[seriesIndex % PALETTE.length]}
              height={Math.max(Math.abs(baseline - y), 1)}
              key={`${series.name}-${index}`}
              rx="2"
              width={Math.max(barWidth - 2, 1)}
              x={plot.left + index * slot + (slot - groupWidth) / 2 + seriesIndex * barWidth}
              y={Math.min(y, baseline)}
            />
          );
        });
      })}
      <text className="chart-axis-label" textAnchor="middle" x={plot.left + plotWidth / 2} y={height - 7}>{chart.x_label}</text>
      <text className="chart-axis-label" textAnchor="middle" transform={`rotate(-90 15 ${plot.top + plotHeight / 2})`} x="15" y={plot.top + plotHeight / 2}>{chart.y_label}</text>
    </svg>
  );
}

function RadarChart({ chart }: { chart: ScientificChart }) {
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
    <svg aria-label={chart.title} className="scientific-chart-svg" role="img" viewBox={`0 0 ${width} ${height}`}>
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
    </div>
  );
}

export default function DataVisualizationWorkbench() {
  const [chartType, setChartType] = useState<CsvChartRequestType>("auto");
  const [result, setResult] = useState<CsvVisualizationResponse | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const numericColumns = useMemo(
    () => result?.columns.filter((column) => column.kind === "numeric") || [],
    [result],
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await analyzeCsv(file, chartType));
    } catch (requestError) {
      setResult(null);
      setError(requestError instanceof Error ? requestError.message : "CSV 分析失败");
    } finally {
      setLoading(false);
    }
  }

  async function copyScript() {
    if (!result) return;
    await navigator.clipboard.writeText(result.chart.matplotlib_script);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
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
          onChange={(event) => setFile(event.target.files?.[0] || null)}
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
          {result.warnings.map((warning) => <p className="citation-warning" key={warning}>{warning}</p>)}
          <div className="csv-summary-strip">
            <span><strong>{result.row_count}</strong> 行</span>
            <span><strong>{result.column_count}</strong> 列</span>
            <span><strong>{numericColumns.length}</strong> 个数值字段</span>
            <span>{result.encoding} · {result.delimiter === "\t" ? "Tab" : result.delimiter} 分隔</span>
          </div>
          <div className="visualization-result-grid">
            <article className="scientific-figure-card">
              <header>
                <div><span>{result.chart.chart_type.toUpperCase()}</span><h3>{result.chart.title}</h3></div>
                <button onClick={() => void copyScript()} type="button">{copied ? "已复制" : "复制 Matplotlib"}</button>
              </header>
              {result.chart.chart_type === "radar3d"
                ? <RadarChart chart={result.chart} />
                : <CartesianChart chart={result.chart} />}
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
