import assert from "node:assert/strict";
import test from "node:test";
import { selectedReadyDocumentIds } from "./documentScope.ts";

const docs = [{ id: "public", status: "ready" }, { id: "private", status: "ready" }, { id: "pending", status: "processing" }];
test("empty scope never silently selects the entire library", () => {
  assert.deepEqual(selectedReadyDocumentIds(docs, []), []);
});
test("scope includes only explicitly selected ready documents", () => {
  assert.deepEqual(selectedReadyDocumentIds(docs, ["public", "pending", "deleted"]), ["public"]);
});
test("scope is stable and deduplicated", () => {
  assert.deepEqual(selectedReadyDocumentIds(docs, ["public", "public"]), ["public"]);
});
