import assert from "node:assert/strict";
import test from "node:test";

import { bboxToPercentRect } from "./pdfGeometry.ts";

test("converts PDF point coordinates to scale-independent percentages", () => {
  assert.deepEqual(bboxToPercentRect([60, 80, 300, 240], 600, 800), {
    left: 10,
    top: 10,
    width: 40,
    height: 20,
  });
});

test("normalizes reversed coordinates and clips them to the page", () => {
  assert.deepEqual(bboxToPercentRect([700, 900, -10, -20], 600, 800), {
    left: 0,
    top: 0,
    width: 100,
    height: 100,
  });
});

test("rejects absent, malformed, and zero-area boxes", () => {
  assert.equal(bboxToPercentRect(null, 600, 800), null);
  assert.equal(bboxToPercentRect([1, 2, 3], 600, 800), null);
  assert.equal(bboxToPercentRect([10, 10, 10, 20], 600, 800), null);
  assert.equal(bboxToPercentRect([10, Number.NaN, 20, 30], 600, 800), null);
});
