import assert from "node:assert/strict";
import test from "node:test";

import { getComparisonFactChecks } from "./comparisonFacts.mjs";

const ids = { transformer: "A", gpt2: "B" };
const testCase = {
  required_cross_document_facts: [{
    fact_id: "two-source-layernorm",
    claim_contains_any_groups: [["Transformer", "Attention Is All You Need"], ["GPT-2"], ["LayerNorm"]],
    sources: [
      { source_filename: "transformer", source_pages: [2], source_text_contains_any: ["LayerNorm(x + Sublayer(x))"] },
      { source_filename: "gpt2", source_pages: [4], source_text_contains_any: ["input of each sub-block"] },
    ],
  }],
};
const evidence = [
  { evidence_id: "E1", document_id: "A", page_number: 2, quote: "LayerNorm(x + Sublayer(x))" },
  { evidence_id: "E2", document_id: "B", page_number: 4, quote: "Layer normalization was moved to the input of each sub-block" },
];
const claim = { text: "Transformer and GPT-2 differ in LayerNorm placement.", evidence_ids: ["E1", "E2"] };
const check = (claims, anchors = evidence) => getComparisonFactChecks(testCase, { claims, evidence: anchors }, ids)["two-source-layernorm"];

test("one displayed claim must cite both source passages", () => assert.equal(check([claim]), true));
test("canonical title alias is accepted without losing source constraints", () => {
  assert.equal(check([{ ...claim, text: "Attention Is All You Need and GPT-2 differ in LayerNorm placement." }]), true);
});
test("two separate single-source claims are not a two-source comparison", () => {
  assert.equal(check([
    { ...claim, text: "Transformer uses LayerNorm.", evidence_ids: ["E1"] },
    { ...claim, text: "GPT-2 uses LayerNorm.", evidence_ids: ["E2"] },
  ]), false);
});
test("wrong page in either paper fails", () => {
  assert.equal(check([claim], evidence.map((item) => ({ ...item, page_number: 7 }))), false);
});
test("uncited available evidence does not cover the missing source", () => {
  assert.equal(check([{ ...claim, evidence_ids: ["E1"] }]), false);
});
test("wrong paper name does not satisfy the comparison", () => {
  assert.equal(check([{ ...claim, text: "Transformer and GPT-3 differ in LayerNorm placement." }]), false);
});
