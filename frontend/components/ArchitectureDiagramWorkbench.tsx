"use client";

import { FormEvent, useMemo, useState } from "react";

import {
  ArchitectureDiagramResponse,
  DiagramFormat,
  DiagramLayout,
  generateArchitectureDiagram,
} from "../lib/api";

const EXAMPLE_TOPOLOGY = `PDF 上传 -> 版式解析 -> Document AST
Document AST -> 混合检索 -> 证据门控 -> LLM 回答
用户问题 -> 混合检索
证据门控 -> 原文锚点`;

const FORMAT_LABELS: Record<DiagramFormat, string> = {
  mermaid: "Mermaid",
  graphviz: "Graphviz",
  tikz: "TikZ",
  matplotlib: "Matplotlib",
};

interface PreviewNode {
  node_id: string;
  label: string;
  x: number;
  y: number;
}

function edgeEndpoints(source: PreviewNode, target: PreviewNode) {
  const deltaX = target.x - source.x;
  const deltaY = target.y - source.y;
  const divisor = Math.max(Math.abs(deltaX) / 82, Math.abs(deltaY) / 30, 1);
  const offsetX = deltaX / divisor;
  const offsetY = deltaY / divisor;
  return {
    x1: source.x + offsetX,
    y1: source.y + offsetY,
    x2: target.x - offsetX,
    y2: target.y - offsetY,
  };
}

function DiagramPreview({ diagram }: { diagram: ArchitectureDiagramResponse }) {
  const nodeById = useMemo(
    () => new Map(diagram.nodes.map((node) => [node.node_id, node])),
    [diagram.nodes],
  );
  return (
    <div className="architecture-preview-scroll">
      <svg
        aria-label={diagram.title}
        className="architecture-preview-svg"
        role="img"
        style={{ minWidth: Math.max(diagram.canvas_width, 680) }}
        viewBox={`0 0 ${diagram.canvas_width} ${diagram.canvas_height}`}
      >
        <title>{diagram.title}</title>
        <defs>
          <marker id="architecture-arrow" markerHeight="8" markerWidth="8" orient="auto" refX="7" refY="4">
            <path d="M0,0 L8,4 L0,8 Z" fill="#718078" />
          </marker>
        </defs>
        {diagram.edges.map((edge, index) => {
          const source = nodeById.get(edge.source);
          const target = nodeById.get(edge.target);
          if (!source || !target) return null;
          const points = edgeEndpoints(source, target);
          return (
            <line
              className="architecture-edge"
              key={`${edge.source}-${edge.target}-${index}`}
              markerEnd="url(#architecture-arrow)"
              {...points}
            />
          );
        })}
        {diagram.nodes.map((node, index) => (
          <g className="architecture-node" key={node.node_id} transform={`translate(${node.x} ${node.y})`}>
            <rect height="60" rx="10" width="164" x="-82" y="-30" />
            <text textAnchor="middle" x="0" y="4">
              {node.label.length > 18 ? `${node.label.slice(0, 17)}…` : node.label}
            </text>
            <title>{node.label}</title>
            <circle cx="-70" cy="-20" r="8" />
            <text className="architecture-node-index" textAnchor="middle" x="-70" y="-17">{index + 1}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

export default function ArchitectureDiagramWorkbench() {
  const [title, setTitle] = useState("证据优先科研助手架构");
  const [idea, setIdea] = useState(EXAMPLE_TOPOLOGY);
  const [layout, setLayout] = useState<DiagramLayout>("left-to-right");
  const [diagram, setDiagram] = useState<ArchitectureDiagramResponse | null>(null);
  const [activeFormat, setActiveFormat] = useState<DiagramFormat>("mermaid");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const activeScript = diagram?.scripts.find((script) => script.format === activeFormat)
    || diagram?.scripts[0];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!idea.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const response = await generateArchitectureDiagram({ title: title.trim(), idea, layout });
      setDiagram(response);
      setActiveFormat(response.scripts[0]?.format || "mermaid");
    } catch (requestError) {
      setDiagram(null);
      setError(requestError instanceof Error ? requestError.message : "架构图生成失败");
    } finally {
      setLoading(false);
    }
  }

  async function copyScript() {
    if (!activeScript) return;
    await navigator.clipboard.writeText(activeScript.content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="architecture-diagram-workbench">
      <form className="architecture-input-panel" onSubmit={submit}>
        <label>
          图标题
          <input maxLength={160} onChange={(event) => setTitle(event.target.value)} value={title} />
        </label>
        <label>
          布局
          <select onChange={(event) => setLayout(event.target.value as DiagramLayout)} value={layout}>
            <option value="left-to-right">从左到右</option>
            <option value="top-down">从上到下</option>
          </select>
        </label>
        <label className="architecture-idea-field">
          组件与关系
          <textarea
            onChange={(event) => setIdea(event.target.value)}
            placeholder="每行使用：组件 A -> 组件 B -> 组件 C"
            rows={5}
            value={idea}
          />
          <small>每行描述一条路径；重复节点与连接会自动合并，名称不会交给模型改写。</small>
        </label>
        <button disabled={loading || !idea.trim() || !title.trim()} type="submit">
          {loading ? "正在生成…" : "生成四种脚本"}
        </button>
      </form>
      {error && <p className="diagram-error">{error}</p>}
      {diagram && (
        <>
          {diagram.warnings.map((warning) => <p className="citation-warning" key={warning}>{warning}</p>)}
          <div className="architecture-result-grid">
            <article className="architecture-preview-card">
              <header>
                <div><span>CANONICAL GRAPH</span><h3>{diagram.title}</h3></div>
                <small>{diagram.nodes.length} 节点 · {diagram.edges.length} 连接</small>
              </header>
              <DiagramPreview diagram={diagram} />
              <p>预览与四种脚本共享同一份规范化节点/边数据，便于论文、README 和技术文档复用。</p>
            </article>
            <aside className="diagram-code-panel">
              <div className="diagram-format-tabs">
                {diagram.scripts.map((script) => (
                  <button
                    aria-pressed={activeFormat === script.format}
                    key={script.format}
                    onClick={() => setActiveFormat(script.format)}
                    type="button"
                  >
                    {FORMAT_LABELS[script.format]}
                  </button>
                ))}
              </div>
              {activeScript && (
                <>
                  <header><code>{activeScript.filename}</code><button onClick={() => void copyScript()} type="button">{copied ? "已复制" : "复制脚本"}</button></header>
                  <pre>{activeScript.content}</pre>
                </>
              )}
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
