import assert from "node:assert/strict";
import test from "node:test";

import { inferTranslationTarget, MAX_TRANSLATION_CHARS } from "./translation.ts";

test("infers the opposite reading language for a selected passage", () => {
  assert.equal(inferTranslationTarget("Self-attention improves translation."), "zh");
  assert.equal(inferTranslationTarget("自注意力机制 improves translation."), "en");
});

test("keeps the frontend selection limit aligned with the API", () => {
  assert.equal(MAX_TRANSLATION_CHARS, 12000);
});
