import type { FutureDirection, FutureWorkAssessment, ReviewEvidence } from "./api";

/** Explicit human decisions override an evidence-limited automatic exclusion. */
export function futureDirectionStatus(direction: FutureDirection): FutureDirection["status"] {
  const decision = direction.confirmation?.decision;
  if (decision === "confirmed_addressed") return "confirmed_addressed";
  if (decision === "open") return "open";
  if (decision === "needs_review") return "possibly_addressed";
  if (direction.assessment?.excluded_from_recommendations) return "evidence_addressed";
  // A newly returned, non-excluding assessment restores the candidate.
  if (direction.status === "evidence_addressed" && direction.assessment) {
    return direction.possibly_addressed_by_document_ids.length ? "possibly_addressed" : "open";
  }
  return direction.status;
}

export function futureProgressLabel(status: FutureWorkAssessment["status"]): string {
  return status === "supported_progress" ? "所选摘录支持进展，仍需核验完整范围"
    : status === "partial_progress" ? "仅有部分进展" : "所选证据不足";
}

/** Never construct a new source or promote a model assessment to resolution. */
export function futureProgressEvidence(
  assessment: FutureWorkAssessment, proof: { evidence_id: string; quote: string },
): ReviewEvidence | null {
  const anchor = assessment.progress_evidence.find((e) => e.evidence_id === proof.evidence_id);
  return anchor && proof.quote.trim().length >= 10 && anchor.quote.includes(proof.quote)
    ? { ...anchor, quote: proof.quote, evidence_kind: "later_progress" } : null;
}
