import assert from "node:assert/strict";
import test from "node:test";
import { reviewEvidencePool } from "./reviewEvidence.ts";

test("legacy review responses keep their original evidence", () => {
  const evidence = [{ evidence_id: "R1" }];
  assert.deepEqual(reviewEvidencePool({ evidence }), evidence);
});
test("local future anchors remain available without replacing model evidence", () => {
  const evidence = [{ evidence_id: "R1" }], future = [{ evidence_id: "F1" }];
  assert.deepEqual(reviewEvidencePool({ evidence, future_work_evidence: future }), [...evidence, ...future]);
  assert.equal(evidence.length, 1);
  assert.equal(future.length, 1);
});
test("empty local pool does not invent a future source", () => {
  assert.deepEqual(reviewEvidencePool({ evidence: [], future_work_evidence: [] }), []);
});
