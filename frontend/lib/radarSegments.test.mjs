import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("./radarSegments.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const module = { exports: {} };
new Function("exports", "module", compiled)(module.exports, module);
const { radarSegments } = module.exports;

test("complete radar closes through the first real metric", () => {
  assert.deepEqual(radarSegments([0, 0.5, 1]), [[0, 1, 2, 0]]);
});
test("missing metric breaks the ring without inventing a center point", () => {
  assert.deepEqual(radarSegments([1, null, 0, 0.5]), [[2, 3, 0]]);
});
test("multiple gaps keep isolated actual zero and singleton observations", () => {
  assert.deepEqual(radarSegments([0, null, 1, null]), [[2], [0]]);
  assert.deepEqual(radarSegments([null, null, null]), []);
});
