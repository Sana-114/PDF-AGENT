import assert from "node:assert/strict";
import test from "node:test";

import {
  defaultReferenceSelections,
  paperCandidateKey,
  uniqueSelectedPapers,
} from "./referenceDiscovery.ts";

const paper = {
  source: "arxiv",
  source_id: "1706.03762v7",
  title: "Attention Is All You Need",
  importable: true,
};

test("selects only confident and importable reference matches by default", () => {
  const selections = defaultReferenceSelections([
    { reference_id: "ref-1", status: "matched", candidates: [{ paper }] },
    { reference_id: "ref-2", status: "uncertain", candidates: [{ paper }] },
    {
      reference_id: "ref-3",
      status: "matched",
      candidates: [{ paper: { ...paper, source_id: "closed", importable: false } }],
    },
  ]);
  assert.deepEqual(Object.keys(selections), ["ref-1"]);
});

test("deduplicates the same paper selected from multiple references", () => {
  const selections = { "ref-1": paper, "ref-8": { ...paper } };
  assert.equal(uniqueSelectedPapers(selections).length, 1);
  assert.equal(paperCandidateKey(paper), "arxiv:1706.03762v7");
});
