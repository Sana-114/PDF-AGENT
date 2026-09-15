import assert from "node:assert/strict";
import test from "node:test";

import {
  adjacentCitationLocation,
  nearestCitationLocation,
} from "./readerNavigation.ts";

const mentions = [
  { citation_id: "cite-a", label: "11", page_number: 2, bbox: [10, 10, 30, 30] },
  { citation_id: "cite-b", label: "11", page_number: 2, bbox: [200, 200, 240, 240] },
  { citation_id: "cite-c", label: "11", page_number: 5, bbox: [50, 50, 80, 80] },
  { citation_id: "cite-d", label: "12", page_number: 2, bbox: [205, 205, 230, 230] },
];

test("chooses the citation block nearest to an inline click", () => {
  assert.equal(
    nearestCitationLocation(mentions, "11", 2, { x: 220, y: 218 })?.citation_id,
    "cite-b",
  );
  assert.equal(nearestCitationLocation(mentions, "99", 2), null);
});

test("cycles citation occurrences in both directions without losing the label", () => {
  assert.equal(adjacentCitationLocation(mentions, "11", "cite-a", 1)?.citation_id, "cite-b");
  assert.equal(adjacentCitationLocation(mentions, "11", "cite-a", -1)?.citation_id, "cite-c");
  assert.equal(adjacentCitationLocation(mentions, "11", null, -1)?.citation_id, "cite-c");
});
