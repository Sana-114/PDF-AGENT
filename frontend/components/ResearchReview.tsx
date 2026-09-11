"use client";

import type {
  CitationGraphRole,
  ResearchReviewResponse,
  ReviewEvidence,
} from "../lib/api";

interface ResearchReviewProps {
  review: ResearchReviewResponse;
  onOpenEvidence: (evidence: ReviewEvidence) => void;
}

const ROLE_LABEL: Record<CitationGraphRole, string> = {
  cornerstone: "基石",
  bridge: "桥接",
  derivative: "衍生",
  isolated: "孤立",
  peripheral: "外围",
};

export default function ResearchReview({ review, onOpenEvidence }: ResearchReviewProps) {
  const evidenceById = new Map(review.evidence.map((item) => [item.evidence_id, item]));
  const openDirections = review.future_directions.filter((item) => item.status === "open");
  const addressedDirections = review.future_directions.filter(
    (item) => item.status === "possibly_addressed",
  );

  function evidenceLink(evidenceId: string, label?: string) {
    const evidence = evidenceById.get(evidenceId);
    if (!evidence) return null;
    return (
      <button
        className="review-evidence-link"
        key={evidenceId}
        onClick={() => onOpenEvidence(evidence)}
        title={evidence.quote}
        type="button"
      >
        {label || evidenceId} · p.{evidence.page_number}
      </button>
    );
  }

  return (
    <div className="research-review-layout">
      <article className="review-narrative">
        <header>
          <span>{review.insufficient_evidence ? "证据不足" : "证据约束综述"}</span>
          <small>{review.model || review.provider}</small>
        </header>
        <p>{review.review || "当前文献没有提取到足够的综述证据。"}</p>
        {review.claims.length > 0 && (
          <div className="review-claims">
            <h3>可验证论点</h3>
            {review.claims.map((claim, index) => (
              <section key={`${index}-${claim.text}`}>
                <p>{claim.text}</p>
                <div>{claim.evidence_ids.map((item) => evidenceLink(item))}</div>
              </section>
            ))}
          </div>
        )}
      </article>

      <aside className="review-paper-timeline">
        <h3>论文脉络 · {review.papers.length}</h3>
        {review.papers.map((paper) => (
          <article key={paper.document_id}>
            <div className="timeline-year">{paper.publication_year || "年份未知"}</div>
            <div>
              <span className={`timeline-role ${paper.graph_role}`}>
                {ROLE_LABEL[paper.graph_role]} · 基石分 {Math.round(paper.foundation_score * 100)}
              </span>
              <h4>{paper.title}</h4>
              <div>
                {paper.overview_evidence_ids.slice(0, 2).map((item) => evidenceLink(item))}
              </div>
            </div>
          </article>
        ))}
      </aside>

      <section className="future-work-board">
        <div className="future-work-heading">
          <div><span>{openDirections.length}</span><strong>仍值得探索</strong></div>
          <div><span>{addressedDirections.length}</span><strong>可能已有进展</strong></div>
          <p>“可能已有进展”不等于已解决，需沿证据继续人工核验。</p>
        </div>
        {review.future_directions.length === 0 && (
          <div className="future-work-empty">当前解析结果中未识别出明确的 Future Work 表述。</div>
        )}
        <div className="future-work-columns">
          <div>
            <h3>开放方向</h3>
            {openDirections.map((direction) => (
              <article className="future-card open" key={direction.direction_id}>
                <div><strong>{direction.direction_id}</strong><span>探索分 {Math.round(direction.exploration_score * 100)}</span></div>
                <p>{direction.text}</p>
                <small>{direction.source_year || "年份未知"} · {direction.source_title}</small>
                <footer>{evidenceLink(direction.source_evidence_id, "Future Work 原文")}</footer>
              </article>
            ))}
          </div>
          <div>
            <h3>待核验的后续进展</h3>
            {addressedDirections.map((direction) => (
              <article className="future-card addressed" key={direction.direction_id}>
                <div><strong>{direction.direction_id}</strong><span>谨慎排除</span></div>
                <p>{direction.text}</p>
                <small>{direction.reason}</small>
                <footer>
                  {evidenceLink(direction.source_evidence_id, "原方向")}
                  {direction.progress_evidence_ids.map((item) => evidenceLink(item, "后续进展"))}
                </footer>
              </article>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
