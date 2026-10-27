import type { ResearchReviewResponse, ReviewEvidence } from "./api";

/** Keep old responses usable while exposing locally extracted direction anchors. */
export function reviewEvidencePool(
  review: Pick<ResearchReviewResponse, "evidence" | "future_work_evidence">,
): ReviewEvidence[] {
  return [...review.evidence, ...(review.future_work_evidence || [])];
}
