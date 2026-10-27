"use client";

import { useMemo, useState } from "react";

import type { EvidenceAnchor, WritingOutlineResponse } from "../lib/api";
import { writingMarkdown } from "../lib/writingMarkdown";

interface WritingWorkbenchProps {
  outline: WritingOutlineResponse;
  onOpenEvidence: (evidence: EvidenceAnchor) => void;
}

export default function WritingWorkbench({ outline, onOpenEvidence }: WritingWorkbenchProps) {
  const [copied, setCopied] = useState(false);
  const evidenceById = useMemo(
    () => new Map(outline.evidence.map((item) => [item.evidence_id, item])),
    [outline.evidence],
  );
  const markdown = useMemo(() => writingMarkdown(outline), [outline]);

  async function copyMarkdown() {
    await navigator.clipboard.writeText(markdown);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  function evidenceButton(evidenceId: string) {
    const evidence = evidenceById.get(evidenceId);
    if (!evidence) return null;
    return (
      <button
        key={evidenceId}
        onClick={() => onOpenEvidence(evidence)}
        title={evidence.quote}
        type="button"
      >
        {evidenceId} · p.{evidence.page_number}
      </button>
    );
  }

  return (
    <div className="writing-workbench">
      <div className="writing-toolbar">
        <div>
          <span>{outline.insufficient_evidence ? "证据不足框架" : "证据约束框架"}</span>
          <small>{outline.model || outline.provider} · {outline.references.length} 条真实 References</small>
        </div>
        <button onClick={() => void copyMarkdown()} type="button">
          {copied ? "已复制" : "复制框架 Markdown"}
        </button>
      </div>
      <div className="writing-grid">
        <article className="writing-draft">
          <h2>{outline.proposed_title}</h2>
          {outline.sections.map((section) => (
            <section key={section.section_id}>
              <h3>{section.title}</h3>
              <pre>{section.content_markdown}</pre>
              <div>{section.evidence_ids.map(evidenceButton)}</div>
            </section>
          ))}
          {!!outline.deferred_claims?.length && (
            <details className="writing-supplement">
              <summary>补充事实 · {outline.deferred_claims.length} 条</summary>
              <p>这些有据陈述未纳入简洁框架。复制正文不包含此区域，请按需要核对取用。</p>
              {outline.deferred_claims.map((claim, index) => (
                <section key={index}>
                  <p>{claim.text}</p>
                  <div>{claim.evidence_ids.map(evidenceButton)}</div>
                </section>
              ))}
            </details>
          )}
        </article>
        <aside className="verified-references">
          <h3>References · 仅保留已核验字段</h3>
          {outline.references.length === 0 && (
            <p className="writing-empty-reference">尚无可用引用，请补充与 idea 相关的论文。</p>
          )}
          {outline.references.map((reference) => (
            <article key={reference.citation_key}>
              <header>
                <strong>[{reference.citation_key}]</strong>
                <span>{reference.provenance}</span>
              </header>
              <p>{reference.formatted_citation}</p>
              <small>已核验：{reference.verified_fields.join(" · ")}</small>
              <div>
                {reference.evidence_ids.slice(0, 3).map(evidenceButton)}
                {reference.metadata_evidence && <button type="button" className="review-evidence-link"
                  onClick={() => onOpenEvidence(reference.metadata_evidence!)}>
                  书目信息 · p.{reference.metadata_evidence.page_number}
                </button>}
                {reference.landing_url && (
                  <a href={reference.landing_url} rel="noreferrer" target="_blank">来源页面 ↗</a>
                )}
              </div>
            </article>
          ))}
        </aside>
      </div>
    </div>
  );
}
