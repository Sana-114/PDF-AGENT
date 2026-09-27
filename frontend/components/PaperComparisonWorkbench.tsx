"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  comparePapers,
  ComparisonRelation,
  CrossPaperComparisonResponse,
  DocumentRecord,
  EvidenceAnchor,
} from "../lib/api";

interface PaperComparisonWorkbenchProps {
  documents: DocumentRecord[];
  onOpenEvidence: (evidence: EvidenceAnchor) => void;
}

const RELATION_LABELS: Record<ComparisonRelation, string> = {
  agreement: "一致",
  difference: "差异",
  conflict: "冲突",
  single_source: "单来源",
  unclassified: "待确认",
};

const SUGGESTIONS = [
  "对比这些论文的核心方法、继承关系与关键差异",
  "对比模型规模、训练设置和实验结果",
  "哪些结论一致，哪些结论存在明确冲突？",
];

export default function PaperComparisonWorkbench({
  documents,
  onOpenEvidence,
}: PaperComparisonWorkbenchProps) {
  const readyDocuments = useMemo(
    () => documents.filter((item) => item.status === "ready"),
    [documents],
  );
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [question, setQuestion] = useState(SUGGESTIONS[0]);
  const [result, setResult] = useState<CrossPaperComparisonResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const readyIds = new Set(readyDocuments.map((item) => item.id));
    setSelectedIds((current) => {
      const retained = current.filter((id) => readyIds.has(id));
      if (retained.length > 0) return retained;
      return readyDocuments.slice(0, 2).map((item) => item.id);
    });
  }, [readyDocuments]);

  function toggleDocument(documentId: string) {
    setSelectedIds((current) => {
      if (current.includes(documentId)) {
        return current.filter((id) => id !== documentId);
      }
      if (current.length >= 6) return current;
      return [...current, documentId];
    });
    setResult(null);
    setError(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cleanQuestion = question.trim();
    if (selectedIds.length < 2 || !cleanQuestion || running) return;
    setRunning(true);
    setError(null);
    try {
      setResult(await comparePapers(cleanQuestion, selectedIds));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "跨论文对比失败");
    } finally {
      setRunning(false);
    }
  }

  const evidenceById = new Map(
    (result?.evidence || []).map((item) => [item.evidence_id, item]),
  );

  return (
    <div className="comparison-workbench">
      <aside className="comparison-selector">
        <header>
          <div>
            <strong>选择对比论文</strong>
            <small>至少 2 篇，最多 6 篇</small>
          </div>
          <span>{selectedIds.length}/6</span>
        </header>
        <div className="comparison-document-list">
          {readyDocuments.length < 2 && (
            <p>至少需要两篇已解析文献才能进行跨论文对比。</p>
          )}
          {readyDocuments.map((document, index) => {
            const selected = selectedIds.includes(document.id);
            return (
              <label
                className={selected ? "selected" : ""}
                data-document-id={document.id}
                key={document.id}
              >
                <input
                  aria-label={`${document.title || document.original_filename} (${document.original_filename})`}
                  checked={selected}
                  disabled={!selected && selectedIds.length >= 6}
                  onChange={() => toggleDocument(document.id)}
                  type="checkbox"
                />
                <span>{index + 1}</span>
                <div>
                  <strong>{document.title || document.original_filename}</strong>
                  <small>
                    {document.arxiv_id ? `arXiv ${document.arxiv_id}` : document.original_filename}
                  </small>
                </div>
              </label>
            );
          })}
        </div>
      </aside>

      <section className="comparison-main">
        <form onSubmit={submit}>
          <label htmlFor="comparison-question">对比维度或问题</label>
          <textarea
            id="comparison-question"
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="例如：BERT 继承了 Transformer Encoder 的哪些设计，又舍弃了什么？"
            rows={3}
            value={question}
          />
          <div className="comparison-form-actions">
            <div>
              {SUGGESTIONS.map((item) => (
                <button key={item} onClick={() => setQuestion(item)} type="button">
                  {item}
                </button>
              ))}
            </div>
            <button
              disabled={running || selectedIds.length < 2 || !question.trim()}
              type="submit"
            >
              {running ? "逐篇检索与核验中…" : "开始证据对比"}
            </button>
          </div>
        </form>

        {error && <div className="comparison-error">{error}</div>}
        {!result && !running && (
          <div className="comparison-placeholder">
            <strong>均衡检索每一篇论文</strong>
            <p>系统不会让篇幅更长的论文垄断证据，并且不会把“没有提到”误判成观点冲突。</p>
          </div>
        )}
        {running && (
          <div className="comparison-placeholder loading">
            <strong>正在逐篇检索并建立来源对照…</strong>
            <p>随后由 {selectedIds.length} 篇论文的原文证据共同约束模型回答。</p>
          </div>
        )}
        {result && !running && (
          <div className="comparison-result">
            <header className="comparison-result-heading">
              <div>
                <span>{result.insufficient_evidence ? "证据不足" : "跨文献证据对比"}</span>
                <small>{result.model || result.provider}</small>
              </div>
              <div className="comparison-stats">
                <span><strong>{result.stats.agreement_count}</strong> 一致</span>
                <span><strong>{result.stats.difference_count}</strong> 差异</span>
                <span><strong>{result.stats.conflict_count}</strong> 冲突</span>
              </div>
            </header>

            <div className="comparison-coverage">
              {result.papers.map((paper) => (
                <div className={paper.coverage} key={paper.document_id}>
                  <span>{paper.coverage === "supported" ? "✓" : "!"}</span>
                  <strong>{paper.title}</strong>
                  <small>
                    {paper.candidate_count} 条候选 → {paper.evidence_count} 条证据 · {paper.evidence_page_count} 页
                  </small>
                </div>
              ))}
            </div>

            <div className="comparison-audit">
              <span>
                <strong>{result.audit.accepted_claim_count}/{result.audit.generated_claim_count}</strong>
                声明通过核验
              </span>
              <span>
                <strong>{Math.round(result.audit.published_claim_citation_coverage * 100)}%</strong>
                发布声明引用覆盖
              </span>
              <span>
                <strong>{result.audit.cross_document_claim_count}</strong>
                跨文献结论
              </span>
              <span>
                <strong>{Math.round(result.audit.evidence_utilization * 100)}%</strong>
                证据利用率
              </span>
            </div>

            <article className="comparison-answer">
              <p>{result.answer}</p>
            </article>

            {result.warnings.length > 0 && (
              <div className="comparison-warnings">
                {result.warnings.map((warning) => <p key={warning}>⚠ {warning}</p>)}
              </div>
            )}

            {result.matrix.length > 0 && (
              <section className="comparison-matrix">
                <header>
                  <strong>结论—论文—原文矩阵</strong>
                  <small>每个单元格只展示该论文实际被引用的证据</small>
                </header>
                <div>
                  <table>
                    <thead>
                      <tr>
                        <th>对比维度</th>
                        {result.papers.map((paper) => (
                          <th key={paper.document_id}>{paper.title}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.matrix.map((row, rowIndex) => (
                        <tr key={`${row.dimension}-${rowIndex}`}>
                          <th>
                            <span className={`relation-${row.relation}`}>
                              {RELATION_LABELS[row.relation]}
                            </span>
                            <strong>{row.dimension}</strong>
                            <small>{row.statement}</small>
                          </th>
                          {row.cells.map((cell) => (
                            <td className={cell.status} key={cell.document_id}>
                              {cell.status === "not_cited" ? (
                                <span>本结论未引用该论文</span>
                              ) : (
                                <>
                                  <p>{cell.summary}</p>
                                  <div>
                                    {cell.evidence_ids.map((evidenceId) => {
                                      const evidence = evidenceById.get(evidenceId);
                                      return (
                                        <button
                                          data-evidence-id={evidenceId}
                                          disabled={!evidence}
                                          key={evidenceId}
                                          onClick={() => evidence && onOpenEvidence(evidence)}
                                          type="button"
                                        >
                                          {evidenceId} · p.{evidence?.page_number}
                                        </button>
                                      );
                                    })}
                                  </div>
                                </>
                              )}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            <div className="comparison-claims">
              {result.claims.map((claim, index) => (
                <article className={`relation-${claim.relation}`} key={`${claim.text}-${index}`}>
                  <header>
                    <span>{claim.dimension} · {RELATION_LABELS[claim.relation]}</span>
                    <small>{claim.document_ids.length} 个来源</small>
                  </header>
                  <p>{claim.text}</p>
                  <div>
                    {claim.evidence_ids.map((evidenceId) => {
                      const evidence = evidenceById.get(evidenceId);
                      return (
                        <button
                          data-evidence-id={evidenceId}
                          disabled={!evidence}
                          key={evidenceId}
                          onClick={() => evidence && onOpenEvidence(evidence)}
                          title={evidence?.quote}
                          type="button"
                        >
                          {evidenceId} · {evidence?.document_title || "来源"} · p.{evidence?.page_number}
                        </button>
                      );
                    })}
                  </div>
                </article>
              ))}
            </div>

            <details className="comparison-trace">
              <summary>查看逐篇检索轨迹</summary>
              {result.trace.map((step, index) => (
                <div key={`${step.skill}-${index}`}>
                  <code>{step.skill}</code>
                  <span>{step.summary}</span>
                  <small>{step.duration_ms} ms</small>
                </div>
              ))}
            </details>
          </div>
        )}
      </section>
    </div>
  );
}
