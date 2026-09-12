import assert from "node:assert/strict";
import test from "node:test";

import {
  buildTranslationMarkdown,
  safeTranslationFilename,
} from "./academicTranslation.ts";

test("exports reviewed bilingual content without changing formulas or citations", () => {
  const markdown = buildTranslationMarkdown({
    title: "RAG 摘要",
    source: "模型满足 $y=x^2$ [11]。",
    translation: "The model satisfies $y=x^2$ [11].",
    status: "reviewed",
    sourceLanguage: "zh",
    targetLanguage: "en",
    exportedAt: new Date("2026-09-12T08:00:00.000Z"),
  });

  assert.match(markdown, /^# RAG 摘要/m);
  assert.match(markdown, /Status: reviewed/);
  assert.match(markdown, /\$y=x\^2\$ \[11\]/g);
  assert.match(markdown, /2026-09-12T08:00:00.000Z/);
});

test("creates a portable markdown filename", () => {
  assert.equal(safeTranslationFilename("实验:结果/最终版"), "实验-结果-最终版.md");
  assert.equal(safeTranslationFilename("<>:\"/\\|?*"), "---------.md");
  assert.equal(safeTranslationFilename(""), "translation.md");
});
