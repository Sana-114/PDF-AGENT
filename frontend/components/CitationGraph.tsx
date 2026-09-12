"use client";

import { KeyboardEvent, useEffect, useMemo, useState } from "react";

import type {
  CitationGraphNode,
  CitationGraphResponse,
  CitationGraphRole,
} from "../lib/api";

interface CitationGraphProps {
  graph: CitationGraphResponse;
  onOpenDocument: (node: CitationGraphNode) => void;
}

interface PositionedNode extends CitationGraphNode {
  x: number;
  y: number;
  radius: number;
}

const ROLE_LABEL: Record<CitationGraphRole, string> = {
  cornerstone: "基石论文",
  bridge: "桥接论文",
  derivative: "边缘衍生",
  isolated: "孤立节点",
  peripheral: "外围论文",
};

function shorten(value: string, length = 24): string {
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}

function positionNodes(nodes: CitationGraphNode[]): PositionedNode[] {
  const cornerstone = nodes.filter((node) => node.role === "cornerstone");
  const remaining = nodes.filter((node) => node.role !== "cornerstone");
  const placeRing = (
    items: CitationGraphNode[],
    centerX: number,
    centerY: number,
    radiusX: number,
    radiusY: number,
  ): PositionedNode[] => items.map((node, index) => {
    const angle = items.length === 1 ? 0 : (Math.PI * 2 * index) / items.length - Math.PI / 2;
    return {
      ...node,
      x: centerX + Math.cos(angle) * radiusX,
      y: centerY + Math.sin(angle) * radiusY,
      radius: Math.min(29, 15 + node.in_degree * 3 + node.foundation_score * 7),
    };
  });
  return [
    ...placeRing(cornerstone, 480, 260, cornerstone.length > 1 ? 92 : 0, 65),
    ...placeRing(remaining, 480, 260, 355, 185),
  ];
}

export default function CitationGraph({ graph, onOpenDocument }: CitationGraphProps) {
  const positioned = useMemo(() => positionNodes(graph.nodes), [graph.nodes]);
  const byId = useMemo(
    () => new Map(positioned.map((node) => [node.document_id, node])),
    [positioned],
  );
  const [selectedId, setSelectedId] = useState(graph.nodes[0]?.document_id || "");
  useEffect(() => {
    if (!graph.nodes.some((node) => node.document_id === selectedId)) {
      setSelectedId(graph.nodes[0]?.document_id || "");
    }
  }, [graph.nodes, selectedId]);
  const selected = byId.get(selectedId) || positioned[0];
  const relatedEdges = graph.edges.filter(
    (edge) => edge.source_document_id === selectedId || edge.target_document_id === selectedId,
  );

  function selectWithKeyboard(event: KeyboardEvent<SVGGElement>, documentId: string) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setSelectedId(documentId);
    }
  }

  if (!graph.nodes.length) {
    return <div className="citation-graph-empty">文献库中还没有已解析论文。</div>;
  }

  return (
    <div className="citation-graph-layout">
      <div className="citation-canvas">
        <svg aria-label="局域引用关系图" role="img" viewBox="0 0 960 520">
          <defs>
            <marker id="citation-arrow" markerHeight="6" markerWidth="7" orient="auto" refX="6" refY="3">
              <path d="M0,0 L0,6 L7,3 z" />
            </marker>
          </defs>
          <g className="citation-edges">
            {graph.edges.map((edge) => {
              const source = byId.get(edge.source_document_id);
              const target = byId.get(edge.target_document_id);
              if (!source || !target) return null;
              return (
                <line
                  className={selectedId && (
                    edge.source_document_id === selectedId || edge.target_document_id === selectedId
                  ) ? "active" : ""}
                  key={edge.edge_id}
                  markerEnd="url(#citation-arrow)"
                  strokeWidth={Math.min(4, 1.2 + edge.mention_count * 0.35)}
                  x1={source.x}
                  x2={target.x}
                  y1={source.y}
                  y2={target.y}
                />
              );
            })}
          </g>
          <g className="citation-nodes">
            {positioned.map((node) => (
              <g
                aria-label={`${ROLE_LABEL[node.role]}：${node.title}`}
                className={`${node.role} ${selectedId === node.document_id ? "active" : ""}`}
                key={node.document_id}
                onClick={() => setSelectedId(node.document_id)}
                onKeyDown={(event) => selectWithKeyboard(event, node.document_id)}
                role="button"
                tabIndex={0}
                transform={`translate(${node.x} ${node.y})`}
              >
                <circle r={node.radius} />
                <text className="node-rank" textAnchor="middle" y="4">
                  {Math.round(node.foundation_score * 100)}
                </text>
                <text className="node-title" textAnchor="middle" y={node.radius + 17}>
                  {shorten(node.title)}
                </text>
              </g>
            ))}
          </g>
        </svg>
        <div className="citation-legend">
          {(Object.entries(ROLE_LABEL) as [CitationGraphRole, string][]).map(([role, label]) => (
            <span className={role} key={role}><i />{label}</span>
          ))}
        </div>
      </div>
      {selected && (
        <aside className="citation-inspector">
          <span className={`citation-role ${selected.role}`}>{ROLE_LABEL[selected.role]}</span>
          <h3>{selected.title}</h3>
          <p>{selected.authors.slice(0, 5).join(" · ") || "作者信息暂缺"}</p>
          <div className="citation-metrics">
            <div><strong>{Math.round(selected.foundation_score * 100)}</strong><span>基石分</span></div>
            <div><strong>{selected.in_degree}</strong><span>被库内引用</span></div>
            <div><strong>{selected.out_degree}</strong><span>引用库内</span></div>
          </div>
          <button onClick={() => onOpenDocument(selected)} type="button">打开原文</button>
          <div className="citation-relations">
            <h4>局域关系 · {relatedEdges.length}</h4>
            {relatedEdges.length === 0 && <p>当前未匹配到库内引用关系。</p>}
            {relatedEdges.slice(0, 6).map((edge) => {
              const outgoing = edge.source_document_id === selected.document_id;
              const peer = byId.get(outgoing ? edge.target_document_id : edge.source_document_id);
              return (
                <article key={edge.edge_id}>
                  <strong>{outgoing ? "引用 →" : "被引用 ←"} {peer ? shorten(peer.title, 34) : "未知论文"}</strong>
                  <span>置信度 {Math.round(edge.match_score * 100)}% · 参考文献第 {edge.reference_pages.join("、")} 页</span>
                  <small>{edge.match_reason}</small>
                </article>
              );
            })}
          </div>
        </aside>
      )}
    </div>
  );
}
