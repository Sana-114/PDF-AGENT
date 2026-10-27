import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { writingMarkdown } from "./writingMarkdown.ts";

const outline = {
  proposed_title: "A source-grounded draft", idea: "Compare methods",
  sections: [{ section_id: "abstract", title: "Abstract", content_markdown: "A fact. [E1]",
    evidence_ids: ["E1"] }],
  references: [{ citation_key: "P1", formatted_citation: "An original paper.",
    provenance: "local_pdf", verified_fields: ["title"], evidence_ids: ["E1"] }],
  claims: [{ text: "A fact.", evidence_ids: ["E1"] }],
  evidence: [{ evidence_id: "E1", page_number: 4, quote: "Original text." }],
  provider: "test", insufficient_evidence: false, warnings: [],
};

test("clipboard preserves the compact draft and references without leaking supplementary facts", () => {
  const compact = writingMarkdown(outline);
  assert.ok(compact.includes("A fact. [E1]") && compact.includes("[P1] An original paper."));
  assert.equal(writingMarkdown({ ...outline,
    deferred_claims: [{ text: "Supplementary detail.", evidence_ids: ["E1"] }] }), compact);
});

test("empty bibliography remains an honest placeholder rather than an invented citation", () => {
  assert.ok(writingMarkdown({ ...outline, references: [] }).includes("暂无经过核验的参考文献"));
});

const require = createRequire(import.meta.url);
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const source = readFileSync(new URL("../components/WritingWorkbench.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;
const exports = {};
runInNewContext(compiled, { exports, require: name => name === "../lib/writingMarkdown"
  ? { writingMarkdown } : require(name) });

test("supplement is collapsible, escaped and has real source-page buttons", () => {
  const html = renderToStaticMarkup(React.createElement(exports.default, {
    outline: { ...outline,
      deferred_claims: [{ text: "<script>unsafe()</script>", evidence_ids: ["E1"] }] },
    onOpenEvidence() {},
  }));
  assert.ok(html.includes("<details") && html.includes("补充事实 · 1 条"));
  assert.ok(html.includes("&lt;script&gt;") && !html.includes("<script>unsafe()"));
  assert.ok(html.includes("E1 · p.4") && html.includes("复制框架 Markdown"));
});

test("legacy output does not show an empty supplementary area", () => {
  const html = renderToStaticMarkup(React.createElement(exports.default, {
    outline, onOpenEvidence() {},
  }));
  assert.ok(!html.includes("补充事实"));
});
