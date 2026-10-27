import assert from "node:assert/strict";
import test from "node:test";
import { reviewClaimGroups } from "./reviewThemes.ts";

test("legacy claims remain visible without invented theme metadata", () => {
  assert.deepEqual(reviewClaimGroups({ claims: [{}] }), [{ title: "可验证论点", indices: [0] }]);
});
test("invalid or duplicate theme references cannot repeat or resurrect a claim", () => {
  const result = reviewClaimGroups({ claims: [{}, {}], themes: [
    { title: "方法", claim_indices: [-1, 0, 0, 5, 0.5] },
    { title: "重复", claim_indices: [0] },
  ] });
  assert.deepEqual(result, [{ title: "方法", indices: [0] }, { title: "其他原文论点", indices: [1] }]);
});
test("an empty result does not render a dangling theme", () => {
  assert.deepEqual(reviewClaimGroups({ claims: [], themes: [{ title: "未发布", claim_indices: [0] }] }), []);
});
