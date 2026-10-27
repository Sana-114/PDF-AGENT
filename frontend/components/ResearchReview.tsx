"use client";

import { useEffect, useState } from "react";
import { assessFutureWorkProgress, saveFutureWorkDecision } from "../lib/api";
import { futureDirectionStatus, futureProgressEvidence, futureProgressLabel } from "../lib/futureProgress";
import { reviewEvidencePool } from "../lib/reviewEvidence";
import { reviewClaimGroups } from "../lib/reviewThemes";
import type {
  CitationGraphRole,
  FutureDirection,
  FutureWorkConfirmation,
  FutureWorkAssessment,
  ResearchReviewResponse,
  EvidenceAnchor,
  ReviewEvidence,
} from "../lib/api";

interface ResearchReviewProps {
  review: ResearchReviewResponse;
  onOpenEvidence: (evidence: EvidenceAnchor) => void;
  onSearchDirection: (text: string) => void;
}

const ROLE_LABEL: Record<CitationGraphRole, string> = {
  cornerstone: "基石",
  bridge: "桥接",
  derivative: "衍生",
  isolated: "孤立",
  peripheral: "外围",
};

export default function ResearchReview({ review, onOpenEvidence, onSearchDirection }: ResearchReviewProps) {
  const [confirmations, setConfirmations] = useState<Record<string, FutureWorkConfirmation>>({});
  const [assessments, setAssessments] = useState<Record<string, FutureWorkAssessment>>({});
  useEffect(() => setConfirmations({}), [review]);
  useEffect(() => setAssessments({}), [review]);
  const evidencePool = reviewEvidencePool(review);
  const evidenceById = new Map(evidencePool.map((item) => [item.evidence_id, item]));
  const directions = review.future_directions.map((item) => {
    const confirmation = confirmations[item.direction_id] || item.confirmation;
    const current = { ...item, confirmation, assessment: assessments[item.direction_id] || item.assessment };
    return { ...current, status: futureDirectionStatus(current) };
  });
  const openDirections = directions.filter((item) => item.status === "open");
  const addressedDirections = directions.filter(
    (item) => item.status === "possibly_addressed",
  );
  const confirmedDirections = directions.filter((item) => item.status === "confirmed_addressed");
  const excludedDirections = directions.filter((item) => item.status === "evidence_addressed");

  function directionActions(direction: FutureDirection) {
    const source = evidenceById.get(direction.source_evidence_id);
    if (!source) return null;
    return <>
      <button className="review-evidence-link" type="button" onClick={() => onSearchDirection(direction.text)}>查找后续论文</button>
      {direction.confirmation && <p className="future-decision-note">人工核验：{direction.confirmation.reason}</p>}
      {direction.confirmation?.progress_evidence.map((item) => <button key={item.evidence_id}
        className="review-evidence-link" type="button" title={item.quote}
        onClick={() => onOpenEvidence(item)}>
        核验依据 · p.{item.page_number}
      </button>)}
      <FutureDecisionEditor direction={direction} source={source}
        candidates={evidencePool.filter((item) => item.document_id !== source.document_id && item.block_ids.length > 0)}
        onSaved={(confirmation) => setConfirmations((current) => ({ ...current, [direction.direction_id]: confirmation }))}
        onAssessed={(assessment) => setAssessments((current) => ({ ...current, [direction.direction_id]: assessment }))}
        onOpenEvidence={onOpenEvidence} />
    </>;
  }

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
        {!!review.citation_relations?.length && (
          <details className="review-claims">
            <summary>局域书目关联 · {review.citation_relations.length}</summary>
            <p>仅表示原文参考条目匹配，不证明方法继承或科研影响。</p>
            {review.citation_relations.map((relation) => (
              <section key={relation.evidence_id}>
                <p>{relation.source_title} → {relation.target_title}</p>
                {evidenceLink(relation.evidence_id, "参考条目")}
              </section>
            ))}
          </details>
        )}
        {review.claims.length > 0 && (
          <div className="review-claims">
            {reviewClaimGroups(review).map((group) => (
              <div className="review-theme" key={group.title}>
                <h3>{group.title}</h3>
                {group.indices.map((index) => {
                  const claim = review.claims[index];
                  return <section key={`${index}-${claim.text}`}>
                    <p>{claim.text}</p>
                    <div>{claim.evidence_ids.map((item) => evidenceLink(item))}</div>
                  </section>;
                })}
              </div>
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
                  {paper.metadata_evidence && <button type="button" className="review-evidence-link"
                    onClick={() => onOpenEvidence(paper.metadata_evidence!)}>
                    年份出处 · p.{paper.metadata_evidence.page_number}
                  </button>}
              </div>
            </div>
          </article>
        ))}
      </aside>

      <section className="future-work-board">
        <div className="future-work-heading">
          <div><span>{openDirections.length}</span><strong>待探索方向</strong></div>
          <div><span>{addressedDirections.length}</span><strong>可能已有进展</strong></div>
          <p>可能已有进展不等于已解决。人工确认 {confirmedDirections.length} 项，保留核验记录。</p>
          {review.future_work_evidence && <p>方向及进展原文独立于生成预算，每篇最多提取 4 条方向候选；不表示完整召回或已确认未解决。</p>}
        </div>
        {review.future_directions.length === 0 && (
          <div className="future-work-empty">当前解析结果中未识别出明确的 Future Work 表述。</div>
        )}
        <div className="future-work-columns">
          <div>
            <h3>开放方向</h3>
            {openDirections.map((direction) => (
              <article className="future-card open" key={direction.direction_id}>
                <div><strong>F{directions.indexOf(direction) + 1}</strong><span>探索分 {Math.round(direction.exploration_score * 100)}</span></div>
                <p>{direction.text}</p>
                <small>{direction.source_year || "年份未知"} · {direction.source_title}</small>
                <footer>{evidenceLink(direction.source_evidence_id, "Future Work 原文")}</footer>
                {directionActions(direction)}
              </article>
            ))}
          </div>
          <div>
            <h3>待核验的后续进展</h3>
            {addressedDirections.map((direction) => (
              <article className="future-card addressed" key={direction.direction_id}>
                <div><strong>F{directions.indexOf(direction) + 1}</strong><span>需核验</span></div>
                <p>{direction.text}</p>
                <small>{direction.reason}</small>
                <footer>
                  {evidenceLink(direction.source_evidence_id, "原方向")}
                  {direction.progress_evidence_ids.map((item) => evidenceLink(item, "后续进展"))}
                </footer>
                {directionActions(direction)}
              </article>
            ))}
          </div>
        </div>
        {excludedDirections.length > 0 && <details className="future-confirmed">
          <summary>原计划已有完成证据 · {excludedDirections.length} 项（自动排除）</summary>
          <p>仅针对原文明确目标和所选后续证据；可通过核验记录改回继续探索。</p>
          {excludedDirections.map((direction) => <article className="future-card" key={direction.direction_id}>
            <p>{direction.text}</p>
            {evidenceLink(direction.source_evidence_id, "原方向")}
            {directionActions(direction)}
          </article>)}
        </details>}
        {confirmedDirections.length > 0 && <details className="future-confirmed">
          <summary>人工确认已有解决证据 · {confirmedDirections.length} 项（不作为开放方向推荐）</summary>
          {confirmedDirections.map((direction) => <article className="future-card" key={direction.direction_id}>
            <p>{direction.text}</p>
            {evidenceLink(direction.source_evidence_id, "原方向")}
            {directionActions(direction)}
          </article>)}
        </details>}
      </section>
    </div>
  );
}

function FutureDecisionEditor({ direction, source, candidates, onSaved, onAssessed, onOpenEvidence }: {
  direction: FutureDirection; source: ReviewEvidence; candidates: ReviewEvidence[];
  onSaved: (confirmation: FutureWorkConfirmation) => void;
  onAssessed: (assessment: FutureWorkAssessment) => void;
  onOpenEvidence: (evidence: ReviewEvidence) => void;
}) {
  const [decision, setDecision] = useState<FutureWorkConfirmation["decision"]>(direction.confirmation?.decision || "needs_review");
  const [reason, setReason] = useState(direction.confirmation?.reason || "");
  const [progressId, setProgressId] = useState(direction.progress_evidence_ids[0] || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [assessing, setAssessing] = useState(false);
  const [assessment, setAssessment] = useState<FutureWorkAssessment | null>(direction.assessment || null);
  useEffect(() => setAssessment(direction.assessment || null), [direction.assessment]);
  async function assess() {
    const evidence = candidates.find((item) => item.evidence_id === progressId);
    if (!evidence) return;
    setAssessing(true); setError("");
    try {
      const result = await assessFutureWorkProgress(direction.direction_id, source, [evidence]);
      setAssessment(result); onAssessed(result);
    }
    catch (failure) { setError(failure instanceof Error ? failure.message : "核对失败"); }
    finally { setAssessing(false); }
  }
  async function save() {
    setSaving(true); setError("");
    try {
      const evidence = candidates.find((item) => item.evidence_id === progressId);
      onSaved(await saveFutureWorkDecision(direction.direction_id, source, decision, reason, evidence ? [evidence] : []));
    } catch (failure) { setError(failure instanceof Error ? failure.message : "保存失败"); }
    finally { setSaving(false); }
  }
  return <details className="future-decision-editor"><summary>核对进展与记录结论</summary>
    <label>方向状态<select value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)}>
      <option value="needs_review">待核验</option><option value="open">继续探索</option>
      <option value="confirmed_addressed">人工确认已有解决证据</option>
    </select></label>
    <label>后续原文证据<select value={progressId} onChange={(event) => setProgressId(event.target.value)}>
      <option value="">未选择（确认已有解决证据时必填）</option>
      {candidates.map((item) => <option key={item.evidence_id} value={item.evidence_id}>
        {item.document_title} · p.{item.page_number} · {item.quote.slice(0, 65)}
      </option>)}
    </select></label>
    {progressId && <q>{candidates.find((item) => item.evidence_id === progressId)?.quote}</q>}
    <button type="button" disabled={!progressId || assessing || saving} onClick={() => void assess()}>
      {assessing ? "正在核对…" : "逐项核对所选进展"}
    </button>
    <small>仅核对原方向和所选后续摘录。全部目标有完成证据并通过独立校审时，自动移出开放推荐；可人工改回。</small>
    {assessment && <section aria-label="后续进展核对结果">
      <p>{futureProgressLabel(assessment.status)}</p>
      {assessment.excluded_from_recommendations && <p>已自动排除该原计划的开放推荐；依据保留在下方。</p>}
      <small>本次核对依据：{assessment.progress_evidence.map((item) => `${item.document_title} · p.${item.page_number}`).join("；")}</small>
      {assessment.facets.map((facet, index) => <article key={index}>
        <q>{facet.goal_quote}</q>
        <p>{facet.status === "addressed" ? "该项有对应证据" : facet.status === "partial" ? "该项仅部分对应" : "尚未建立对应证据"}：{facet.explanation}</p>
        {facet.proof.map((proof, p) => {
          const evidence = futureProgressEvidence(assessment, proof);
          return evidence && <button className="review-evidence-link" type="button" key={p}
            title={proof.quote} onClick={() => onOpenEvidence(evidence)}>后续原文 · p.{evidence.page_number}</button>;
        })}
      </article>)}
      <details><summary>核对范围与限制</summary>{assessment.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</details>
    </section>}
    <label>核验说明<textarea value={reason} rows={3} maxLength={2000}
      placeholder="说明后续工作解决了什么、还有哪些边界（至少 10 个字符）"
      onChange={(event) => setReason(event.target.value)} /></label>
    <small>先查找并导入后续论文，再重新整理综述；元数据或主题相似不是解决证据。</small>
    <button type="button" disabled={saving || reason.trim().length < 10 || (decision === "confirmed_addressed" && !progressId)} onClick={() => void save()}>
      {saving ? "保存中…" : "保存核验记录"}
    </button>{error && <p role="alert">{error}</p>}
  </details>;
}
