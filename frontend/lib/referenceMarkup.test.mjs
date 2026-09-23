import assert from "node:assert/strict";
import test from "node:test";

import { linkifyNumericCitations } from "./referenceMarkup.ts";

test("links real alphabetic bibliography keys, not synthetic author-year identifiers", () => {
  const markup = linkifyNumericCitations("[AB+20] [unknown] [author-year-2]", new Set(["AB+20", "author-year-2"]));
  assert.equal(markup, '<mark class="pdf-citation-link" data-reference-label="AB+20">[AB+20]</mark> [unknown] [author-year-2]');
});

test("marks only citations with a parsed reference entry", () => {
  assert.equal(
    linkifyNumericCitations("Prior work [11] differs from [99].", new Set(["11"])),
    'Prior work <mark class="pdf-citation-link" data-reference-label="11">[11]</mark> differs from [99].',
  );
});

test("escapes untrusted PDF text before producing citation markup", () => {
  assert.equal(
    linkifyNumericCitations('<img src=x> & [2]', new Set(["2"])),
    '&lt;img src=x&gt; &amp; <mark class="pdf-citation-link" data-reference-label="2">[2]</mark>',
  );
});
