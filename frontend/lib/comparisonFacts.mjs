function containsAny(text, options) {
  const normalize = (value) => value.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  const searchable = normalize(text);
  return options.some((option) => searchable.includes(normalize(option)));
}

/** Verify the displayed response, including one-claim, two-source comparisons. */
export function getComparisonFactChecks(testCase, response, documentIdsByFilename) {
  const anchors = new Map(response.evidence.map((anchor) => [anchor.evidence_id, anchor]));
  const matchesSource = (anchor, source) => Boolean(
    anchor
    && anchor.document_id === documentIdsByFilename[source.source_filename]
    && source.source_pages.includes(anchor.page_number)
    && containsAny(anchor.quote, source.source_text_contains_any),
  );
  const checks = Object.fromEntries((testCase.required_claim_facts || []).map((fact) => [
    fact.fact_id,
    response.claims.some((claim) =>
      containsAny(claim.text, fact.claim_contains_any)
      && (fact.claim_contains_all || []).every((term) => containsAny(claim.text, [term]))
      && claim.evidence_ids.some((id) => matchesSource(anchors.get(id), fact)),
    ),
  ]));
  for (const fact of testCase.required_cross_document_facts || []) {
    checks[fact.fact_id] = response.claims.some((claim) =>
      fact.claim_contains_any_groups.every((group) => containsAny(claim.text, group))
      && fact.sources.every((source) =>
        claim.evidence_ids.some((id) => matchesSource(anchors.get(id), source)),
      ),
    );
  }
  return checks;
}
