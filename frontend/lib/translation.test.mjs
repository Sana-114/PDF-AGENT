import assert from "node:assert/strict";
import test from "node:test";

import {
  inferTranslationTarget,
  mapSynchronizedScroll,
  MAX_TRANSLATION_CHARS,
} from "./translation.ts";

test("infers the opposite reading language for a selected passage", () => {
  assert.equal(inferTranslationTarget("Self-attention improves translation."), "zh");
  assert.equal(inferTranslationTarget("自注意力机制 improves translation."), "en");
});

test("keeps the frontend selection limit aligned with the API", () => {
  assert.equal(MAX_TRANSLATION_CHARS, 12000);
});

test("maps scroll progress between unequal bilingual panes", () => {
  assert.equal(mapSynchronizedScroll(400, 1000, 200, 600, 200), 200);
  assert.equal(mapSynchronizedScroll(900, 1000, 200, 600, 200), 400);
  assert.equal(mapSynchronizedScroll(0, 200, 200, 600, 200), null);
});
