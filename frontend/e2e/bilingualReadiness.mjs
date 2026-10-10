// Real reader gate. Only a pinned public first page and one native short selection.
// Default: cached page only, zero model calls. --allow-cloud must be authorized.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { waitForPaintedPdfPage } from "./pdfRenderReady.mjs";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const args = process.argv.slice(2);
const value = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const mode = value("--case", "cached");
assert.ok(["cached", "all", "page", "selection"].includes(mode));
const cloudAllowed = args.includes("--allow-cloud");
assert.ok(mode === "cached" || cloudAllowed, "New generation requires explicit authorization");
const base = value("--url", "http://localhost:3300");
const api = value("--api", "http://localhost:8000/api/v1");
for (const url of [base, api]) assert.ok(["localhost", "127.0.0.1", "[::1]"].includes(new URL(url).hostname));
const output = path.resolve(value("--output", path.join(repo, `backend/tmp/bilingual-readiness/run-${Date.now()}.json`)));
const imageDir = path.join(path.dirname(output), path.basename(output, ".json"));
await mkdir(imageDir, { recursive: true });
const report = { evaluation_kind: "real_bilingual_reader", timestamp: new Date().toISOString(), status: "failed", mode, cloud_authorized: cloudAllowed, request_counts: {}, cases: [], blocked_requests: [], page_errors: [] };
const browser = await chromium.launch({ channel: process.platform === "win32" ? "msedge" : "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.setDefaultTimeout(30_000);
page.on("pageerror", (error) => report.page_errors.push(error.message));
let publicId, selectedText;
let cachedBefore = false;
await page.route("**/api/v1/**", async (route) => {
  const request = route.request();
  if (request.method() !== "POST") return route.continue();
  const endpoint = new URL(request.url()).pathname.split("/api/v1")[1];
  const body = request.postDataJSON();
  const pageEndpoint = `/documents/${publicId}/translations/pages/1`;
  let allowed = !report.request_counts[endpoint];
  if (endpoint === pageEndpoint) allowed &&= (cloudAllowed && ["all", "page"].includes(mode) || cachedBefore) && body.target_language === "zh";
  else if (endpoint === "/agent/translate") allowed &&= cloudAllowed && ["all", "selection"].includes(mode) && Boolean(selectedText) && body.text === selectedText && body.text.length <= 240 && body.target_language === "zh";
  else allowed = false;
  if (!allowed) { report.blocked_requests.push(endpoint); return route.abort(); }
  report.request_counts[endpoint] = 1;
  return route.continue(); // No response mocking or prerecorded answers.
});
const persist = () => writeFile(output, JSON.stringify(report, null, 2));
async function capture(name) {
  const destination = path.join(imageDir, `${name}.png`);
  await page.screenshot({ path: destination, fullPage: false });
  return destination;
}
const scrollState = () => page.evaluate(() => {
  const snapshot = (selector) => {
    const node = document.querySelector(selector);
    const range = node.scrollHeight - node.clientHeight;
    return { top: node.scrollTop, range, ratio: range ? node.scrollTop / range : 0 };
  };
  return { left: snapshot(".pdf-reader-viewport"), right: snapshot(".pdf-bilingual-pane") };
});
async function waitForScrollPair() {
  await page.waitForFunction(() => {
    const a = document.querySelector(".pdf-reader-viewport");
    const b = document.querySelector(".pdf-bilingual-pane");
    const ar = a.scrollHeight - a.clientHeight, br = b.scrollHeight - b.clientHeight;
    return ar > 50 && br > 50 && Math.abs(a.scrollTop / ar - b.scrollTop / br) < .025;
  });
  return scrollState();
}
try {
  const corpus = JSON.parse(await readFile(path.join(repo, "docs/pdf-regression-corpus.json"), "utf8"));
  const expected = corpus.downloads.find((item) => item.filename === "1706.03762v1.pdf");
  const libraryResponse = await page.request.get(`${api}/documents`);
  assert.equal(libraryResponse.status(), 200);
  const doc = (await libraryResponse.json()).items.find((item) => item.original_filename === expected.filename && item.status === "ready");
  assert.ok(doc);
  assert.equal(doc.sha256, expected.sha256);
  publicId = doc.id;
  report.public_document = { id: doc.id, filename: doc.original_filename, sha256: doc.sha256 };
  const cache = await page.request.get(`${api}/documents/${publicId}/translations/zh/pages/1`);
  assert.ok([200, 404].includes(cache.status()));
  cachedBefore = cache.status() === 200;
  report.cached_before = cachedBefore;
  if (["cached", "selection"].includes(mode)) assert.ok(cachedBefore, "A previously generated public page is required; do not generate implicitly");

  await page.goto(base, { waitUntil: "networkidle" });
  await page.getByLabel("工作区导航").getByRole("button", { name: /文献库/ }).click();
  const card = page.locator(".document-card").filter({ hasText: "arXiv:1706.03762v1" }).first();
  await card.getByRole("button", { name: "在线阅读", exact: true }).click();
  const reader = page.getByRole("dialog", { name: /阅读/ });
  await waitForPaintedPdfPage(reader, 1);
  const endpoint = `/documents/${publicId}/translations/pages/1`;
  const [response] = await Promise.all([
    page.waitForResponse((item) => item.url().endsWith(endpoint) && item.request().method() === "POST", { timeout: 180_000 }),
    reader.getByRole("button", { name: "中英", exact: true }).click(),
  ]);
  const payload = await response.json();
  const pageCase = { case_id: "page_translation", http_status: response.status(), status: "pending", generation: cachedBefore ? "existing_page_cache" : "new_model_request", response: payload };
  report.cases.push(pageCase);
  await persist();
  assert.equal(response.status(), 200);
  assert.equal(payload.document_id, publicId);
  assert.equal(payload.page_number, 1);
  assert.equal(payload.provider, "deepseek");
  assert.equal(payload.model, "deepseek-flash");
  assert.equal(payload.truncated, false);
  assert.ok(payload.segments.length > 1);
  assert.equal(new Set(payload.segments.map((item) => item.block_id)).size, payload.segments.length);
  await reader.locator(".pdf-bilingual-segments article").first().waitFor();
  assert.equal(await reader.locator(".pdf-bilingual-segments article").count(), payload.segments.length);
  for (const item of payload.segments) {
    assert.ok(item.source_text.trim() && item.translation.trim());
    const segment = reader.locator(`.pdf-bilingual-segments article[data-block-id="${item.block_id}"]`);
    assert.equal(await segment.locator("p").innerText(), item.translation);
    assert.equal(await segment.locator("blockquote").textContent(), item.source_text);
  }
  pageCase.screenshot = await capture("page-bilingual");
  pageCase.status = "passed";
  await persist();

  // Test real wheel events in both directions, not only mapSynchronizedScroll math.
  for (let i = 0; i < 2; i++) await reader.getByRole("button", { name: "放大", exact: true }).click();
  await waitForPaintedPdfPage(reader, 1);
  await page.waitForFunction(() => [".pdf-reader-viewport", ".pdf-bilingual-pane"].every((selector) => {
    const node = document.querySelector(selector);
    return node.scrollHeight - node.clientHeight > 50;
  }));
  const linked = reader.getByRole("button", { name: "联动", exact: true });
  assert.equal(await linked.getAttribute("aria-pressed"), "true");
  const beforeLeft = await scrollState();
  await reader.locator(".pdf-reader-viewport").hover();
  await page.mouse.wheel(0, Math.max(30, beforeLeft.left.range * .35));
  await page.waitForFunction((prior) => document.querySelector(".pdf-reader-viewport").scrollTop > prior + 20, beforeLeft.left.top);
  const fromLeft = await waitForScrollPair();
  assert.ok(fromLeft.left.top > 20, "Source must actually scroll");
  await reader.locator(".pdf-bilingual-pane").hover();
  await page.mouse.wheel(0, -Math.min(180, fromLeft.right.top * .5));
  await page.waitForFunction((prior) => document.querySelector(".pdf-bilingual-pane").scrollTop < prior - 20, fromLeft.right.top);
  const fromRight = await waitForScrollPair();
  assert.ok(fromRight.right.top < fromLeft.right.top - 20, "Translation must actually scroll");
  await linked.click();
  assert.equal(await linked.getAttribute("aria-pressed"), "false");
  const beforePaused = await scrollState();
  await reader.locator(".pdf-bilingual-pane").hover();
  await page.mouse.wheel(0, 180);
  await page.waitForFunction((prior) => document.querySelector(".pdf-bilingual-pane").scrollTop > prior + 20, beforePaused.right.top);
  const afterPaused = await scrollState();
  assert.ok(Math.abs(afterPaused.left.top - beforePaused.left.top) < 2);
  report.cases.push({ case_id: "reciprocal_scroll", status: "passed", mapping: "normalized_scroll_progress_not_semantic_paragraph_alignment", from_left: fromLeft, from_right: fromRight, paused_before: beforePaused, paused_after: afterPaused });
  await persist();

  if (["all", "selection"].includes(mode)) {
    // Select a real short PDF text-layer excerpt. Do not inject source text or answers.
    await reader.locator(".pdf-reader-viewport").evaluate((node) => node.scrollTop = 0);
    const span = reader.locator(".react-pdf__Page__textContent span").filter({ hasText: /We propose/ }).first();
    await span.scrollIntoViewIfNeeded();
    selectedText = await span.evaluate((node) => {
      const textNode = [...node.childNodes].find((child) => child.nodeType === Node.TEXT_NODE);
      if (!textNode) throw new Error("Native PDF text node missing");
      const range = document.createRange();
      range.selectNodeContents(textNode);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      node.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      return selection.toString().trim();
    });
    assert.ok(selectedText.length > 15 && selectedText.length <= 240);
    const [selectedResponse] = await Promise.all([
      page.waitForResponse((item) => item.url().endsWith("/agent/translate") && item.request().method() === "POST", { timeout: 180_000 }),
      reader.locator(".pdf-translate-action").click(),
    ]);
    const translated = await selectedResponse.json();
    const selectedCase = { case_id: "native_selection_translation", http_status: selectedResponse.status(), source_text: selectedText, response: translated, status: "pending" };
    report.cases.push(selectedCase);
    await persist();
    assert.equal(selectedResponse.status(), 200);
    assert.equal(translated.provider, "deepseek");
    assert.equal(translated.model, "deepseek-flash");
    assert.ok(/[\u3400-\u9fff]/u.test(translated.translation));
    await reader.locator(".pdf-translation-result p").waitFor();
    assert.equal(await reader.locator(".pdf-translation-panel blockquote").innerText(), selectedText);
    assert.equal(await reader.locator(".pdf-translation-result p").innerText(), translated.translation);
    selectedCase.screenshot = await capture("native-selection");
    selectedCase.status = "passed";
  }
  assert.deepEqual(report.blocked_requests, []);
  assert.deepEqual(report.page_errors, []);
  report.status = "passed";
} catch (error) {
  report.error = error.stack || error.message;
  try { report.failure_scroll_state = await scrollState(); } catch { /* Reader not opened. */ }
  try { report.failure_screenshot = await capture("failure"); } catch { /* Closed browser. */ }
  process.exitCode = 1;
} finally {
  await persist();
  await browser.close();
  console.log(JSON.stringify({ status: report.status, report: output, requests: report.request_counts, cases: report.cases.map(({ case_id, status }) => ({ case_id, status })), error: report.error }, null, 2));
}
