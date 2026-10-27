import type { ResearchReviewResponse } from "./api";

export function reviewClaimGroups(
  review: Pick<ResearchReviewResponse, "claims" | "themes">,
) {
  const assigned = new Set<number>();
  const groups = (review.themes || []).flatMap((theme) => {
    const indices = theme.claim_indices.filter((index) => {
      if (!Number.isInteger(index) || index < 0 || index >= review.claims.length || assigned.has(index)) return false;
      assigned.add(index);
      return true;
    });
    return indices.length ? [{ title: theme.title, indices }] : [];
  });
  const ungrouped = review.claims.map((_, index) => index).filter((index) => !assigned.has(index));
  if (ungrouped.length) groups.push({ title: groups.length ? "其他原文论点" : "可验证论点", indices: ungrouped });
  return groups;
}
