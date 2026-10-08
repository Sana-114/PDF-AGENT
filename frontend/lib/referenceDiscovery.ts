import type { PaperCandidate, ReferenceResolution } from "./api";

export type ReferenceSelections = Record<string, PaperCandidate>;

export function paperCandidateKey(candidate: PaperCandidate): string {
  return `${candidate.source}:${candidate.source_id}`;
}

export function defaultReferenceSelections(
  resolutions: ReferenceResolution[],
): ReferenceSelections {
  return Object.fromEntries(
    resolutions.flatMap((resolution) => {
      const candidate = resolution.candidates[0]?.paper;
      const recommended = !resolution.citation_value || resolution.citation_value.recommended;
      return resolution.status === "matched" && candidate?.importable && recommended
        ? [[resolution.reference_id, candidate]]
        : [];
    }),
  );
}

export function uniqueSelectedPapers(selections: ReferenceSelections): PaperCandidate[] {
  const unique = new Map<string, PaperCandidate>();
  Object.values(selections).forEach((candidate) => {
    unique.set(paperCandidateKey(candidate), candidate);
  });
  return [...unique.values()];
}
