import assert from "node:assert/strict";
import test from "node:test";
import { futureDirectionStatus, futureProgressLabel, futureProgressEvidence } from "./futureProgress.ts";

test("automatic exclusion is bounded and every explicit human decision overrides it", () => {
  const item = { status: "open", assessment: { excluded_from_recommendations: true },
    possibly_addressed_by_document_ids: [] };
  assert.equal(futureDirectionStatus(item), "evidence_addressed");
  for (const [decision, expected] of [["open", "open"], ["needs_review", "possibly_addressed"],
    ["confirmed_addressed", "confirmed_addressed"]]) {
    assert.equal(futureDirectionStatus({ ...item, confirmation: { decision } }), expected);
  }
  assert.equal(futureDirectionStatus({ ...item, status: "evidence_addressed",
    assessment: { excluded_from_recommendations: false } }), "open");
});

test("even all supported model progress is not labelled resolved", () => {
  for (const status of ["supported_progress", "partial_progress", "insufficient"]) {
    assert.ok(!futureProgressLabel(status).includes("已解决"));
  }
  assert.ok(futureProgressLabel("partial_progress").includes("部分"));
});

test("progress jump preserves original identity and exact selected proof", () => {
  const anchor = { evidence_id: "P1", document_id: "later", page_number: 4,
    block_ids: ["p4-b1"], bbox: [1, 2, 3, 4], quote: "We evaluate retrieval for images in experiments." };
  const proof = { evidence_id: "P1", quote: "retrieval for images" };
  const result = futureProgressEvidence({ progress_evidence: [anchor] }, proof);
  assert.equal(result.document_id, anchor.document_id);
  assert.equal(result.quote, proof.quote);
  assert.deepEqual(result.bbox, anchor.bbox);
  assert.equal(anchor.quote, "We evaluate retrieval for images in experiments.");
});

test("unknown or invented proofs never create a page jump", () => {
  const assessment = { progress_evidence: [{ evidence_id: "P1", quote: "Image retrieval only." }] };
  assert.equal(futureProgressEvidence(assessment, { evidence_id: "P2", quote: "Image retrieval only." }), null);
  assert.equal(futureProgressEvidence(assessment, { evidence_id: "P1", quote: "All audio and video are solved." }), null);
});

test("two distinct proofs in one original paragraph keep separate literal jump excerpts", () => {
  const anchor = { evidence_id: "P1", document_id: "later", page_number: 4,
    block_ids: ["p4-b1"], bbox: [1, 2, 3, 4],
    quote: "We evaluate image retrieval. We report the actual sample count." };
  const assessment = { progress_evidence: [anchor] };
  const quotes = ["We evaluate image retrieval.", "We report the actual sample count."];
  const jumps = quotes.map((quote) => futureProgressEvidence(assessment, { evidence_id: "P1", quote }));
  assert.deepEqual(jumps.map((jump) => jump.quote), quotes);
  assert.ok(jumps.every((jump) => jump.document_id === "later" && jump.page_number === 4));
  assert.deepEqual(jumps[0].bbox, jumps[1].bbox);
  assert.notEqual(jumps[0].quote, jumps[1].quote);
  assert.equal(anchor.quote, "We evaluate image retrieval. We report the actual sample count.");
});
