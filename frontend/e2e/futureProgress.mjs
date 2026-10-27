/** Current UI/API, cached real assessments and one new public-excerpt model call. */
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { waitForPaintedPdfPage } from "./pdfRenderReady.mjs";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const output = path.resolve(process.argv[2] || "");
const rel = path.relative(path.join(repo, "backend/tmp"), output);
assert.ok(rel && !rel.startsWith("..") && !path.isAbsolute(rel));
await mkdir(output, { recursive: false });
const fixture = JSON.parse(await readFile(path.join(repo,
  "backend/tmp/official-scope-repair-2026-10-19/future-assessment-final/fixture.json"), "utf8"));
const old = fixture.find((r) => r.filename === "1706.03762v1.pdf");
const later = fixture.find((r) => r.filename === "2010.11929v2.pdf");
const report = { status: "running", checks: {}, page_errors: [], forbidden_requests: [],
  injected_errors: 0, actual_assessment_posts: 0, production_library_modified: false };
const save = () => writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(45_000);
page.on("pageerror", (error) => report.page_errors.push(error.message));
let inject = false;
await page.route("**/api/v1/**", async (route) => {
  const req = route.request(), url = new URL(req.url());
  if (["GET", "HEAD", "OPTIONS"].includes(req.method())) return route.continue();
  if (req.method() === "POST" && url.pathname.endsWith("/reviews/generate")) {
    assert.equal(req.postDataJSON().generation_mode, "extractive");
    assert.deepEqual(new Set(req.postDataJSON().document_ids), new Set([old.document_id, later.document_id]));
    return route.continue();
  }
  if (req.method() === "POST" && url.pathname.endsWith("/assess")) {
    if (inject) {
      inject = false; report.injected_errors++;
      return route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"验收注入：核对服务暂不可用"}' });
    }
    report.actual_assessment_posts++;
    assert.equal(report.actual_assessment_posts, 1, "No automatic duplicate call");
    const payload = req.postDataJSON();
    assert.equal(payload.source.document_id, old.document_id);
    assert.equal(payload.progress.length, 1);
    assert.equal(payload.progress[0].document_id, later.document_id);
    return route.continue();
  }
  report.forbidden_requests.push(url.pathname);
  return route.abort();
});

try {
  await page.goto("http://127.0.0.1:3341/#research-review");
  const section = page.locator("#research-review");
  await section.locator(".document-scope-picker summary").click();
  for (const row of [old, later]) {
    await section.locator(`label[data-document-id="${row.document_id}"] input`).check();
  }
  await section.getByLabel("仅整理原文（不调用 LLM）").check();
  const response = page.waitForResponse((r) => r.url().endsWith("/reviews/generate") && r.request().method() === "POST");
  await section.getByRole("button", { name: "整理综述", exact: true }).click();
  const review = await (await response).json();
  const card = section.locator(".future-card").filter({ hasText: "modalities other than text" }).first();
  await card.locator(".future-decision-editor > summary").click();
  await card.getByText("仅有部分进展", { exact: true }).waitFor();
  report.checks.cached_real_assessment_visible = true;
  const candidate = [...review.evidence, ...review.future_work_evidence].find((e) =>
    e.document_id === later.document_id && e.block_ids.length && e.quote.includes("sequences of image patches"));
  assert.ok(candidate);
  const editor = card.locator(".future-decision-editor");
  await editor.getByLabel("后续原文证据").selectOption(candidate.evidence_id);
  inject = true;
  await editor.getByRole("button", { name: "逐项核对所选进展" }).click();
  await editor.getByText("验收注入：核对服务暂不可用", { exact: false }).waitFor();
  report.checks.error_visible_without_silent_retry = report.actual_assessment_posts === 0;
  const assessed = page.waitForResponse((r) => r.url().endsWith("/assess") && r.status() === 200);
  await editor.getByRole("button", { name: "逐项核对所选进展" }).click();
  const result = await (await assessed).json();
  assert.equal(result.status, "partial_progress");
  assert.equal(result.automatically_resolved, false);
  await editor.getByText("仅有部分进展", { exact: true }).waitFor();
  assert.equal(await editor.getByText("验收注入：核对服务暂不可用", { exact: false }).count(), 0);
  report.checks.actual_generation_and_manual_recovery = true;
  await page.screenshot({ path: path.join(output, "desktop.png"), fullPage: false });
  await editor.getByRole("button", { name: /后续原文 · p\./ }).first().click();
  const dialog = page.getByRole("dialog");
  await waitForPaintedPdfPage(dialog, 1);
  assert.equal(await dialog.locator("#pdf-page-number").inputValue(), "1");
  report.checks.proof_jump_has_real_pdf_pixels = true;
  await page.screenshot({ path: path.join(output, "source-jump.png") });
  await dialog.getByRole("button", { name: "关闭阅读器" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await card.scrollIntoViewIfNeeded();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  report.checks.mobile_no_horizontal_overflow = true;
  await page.screenshot({ path: path.join(output, "mobile.png") });
  report.checks.no_javascript_errors = report.page_errors.length === 0;
  report.checks.no_unrelated_mutations = report.forbidden_requests.length === 0;
  report.status = "passed_scoped_ui_checks";
} catch (error) {
  report.status = "failed"; report.error = error.message;
  await page.screenshot({ path: path.join(output, "failure.png") }).catch(() => {});
} finally {
  await save(); await browser.close();
}
console.log(JSON.stringify(report));
if (report.status === "failed") process.exitCode = 1;
