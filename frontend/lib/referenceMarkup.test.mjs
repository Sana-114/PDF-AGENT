import assert from "node:assert/strict";
import test from "node:test";

import { linkifyNumericCitations, linkifyPageCitations } from "./referenceMarkup.ts";

test("links real alphabetic bibliography keys, not synthetic author-year identifiers", () => {
  const markup = linkifyNumericCitations("[AB+20] [unknown] [author-year-2]", new Set(["AB+20", "author-year-2"]));
  assert.equal(markup, '<mark class="pdf-citation-link" data-reference-label="AB+20">[AB+20]</mark> [unknown] [author-year-2]');
});

test("author-year citation crosses PDF text items without replacing original text", () => {
  const items = ["Prior work (Smith et", "al., 2020) and [2]."];
  const markup = linkifyPageCitations(items,
    [{ label: "author-year-1", citation_text: "Smith et al.,\n2020" }], new Set(["author-year-1", "2"]));
  assert.match(markup[0], /data-reference-label="author-year-1">Smith et<\/mark>/);
  assert.match(markup[1], />al., 2020<\/mark>\) and/);
  assert.match(markup[1], /data-reference-label="2"/);
});

test("ambiguous citations and unrecognized labels remain plain text", () => {
  assert.deepEqual(linkifyPageCitations(["Smith (2020)"], [
    { label: "a", citation_text: "Smith (2020" }, { label: "b", citation_text: "Smith (2020" },
  ], new Set(["a", "b"])), ["Smith (2020)"]);
  assert.deepEqual(linkifyPageCitations(["Smith (2020)"], [
    { label: "unknown", citation_text: "Smith (2020" },
  ], new Set()), ["Smith (2020)"]);
});

test("author-year rendering escapes attributes and does not match a year suffix", () => {
  const [markup] = linkifyPageCitations(["<b> Smith (2020a); Smith (2020)"], [
    { label: 'x" onclick="bad', citation_text: "Smith (2020" },
  ], new Set(['x" onclick="bad']));
  assert.match(markup, /^&lt;b&gt; Smith \(2020a\);/);
  assert.equal((markup.match(/<mark/g) || []).length, 1);
  assert.match(markup, /data-reference-label="x&quot; onclick=&quot;bad"/);
});

test("marks only citations with a parsed reference entry", () => {
  assert.equal(
    linkifyNumericCitations("Prior work [11] differs from [99].", new Set(["11"])),
    'Prior work <mark class="pdf-citation-link" data-reference-label="11">[11]</mark> differs from [99].',
  );
});

test("numeric citation split across font runs remains clickable", () => {
  const markup = linkifyPageCitations(["prior[", "29", "]more"],
    [{ label: "29", citation_text: "[29]" }], new Set(["29"]));
  assert.equal(markup.filter(html => html.includes('data-reference-label="29"')).length, 3);
  assert.equal(markup.map(html => html.replace(/<[^>]*>/g, "")).join(""), "prior[29]more");
});

test("escapes untrusted PDF text before producing citation markup", () => {
  assert.equal(
    linkifyNumericCitations('<img src=x> & [2]', new Set(["2"])),
    '&lt;img src=x&gt; &amp; <mark class="pdf-citation-link" data-reference-label="2">[2]</mark>',
  );
});
