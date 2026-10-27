/** Actual cached public assessments in the current production UI; no cloud calls. */
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { waitForPaintedPdfPage } from "./pdfRenderReady.mjs";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const root = path.join(repo, "backend/tmp/official-completion-2026-10-26");
const label = process.env.PDFAGENT_FUTURE_BROWSER_LABEL || "future-browser";
assert.match(label, /^[a-z0-9-]+$/);
const output = path.join(root, label);
await mkdir(output, { recursive: false });
const fixture = JSON.parse(await readFile(path.join(repo,
  "backend/tmp/official-recording-release-2026-10-24/data/fixture.json"), "utf8"));
const files = ["2005.14165v4.pdf", "2203.02155v1.pdf", "1706.03762v1.pdf", "2010.11929v2.pdf",
  "2106.09685v2.pdf", "2305.18290v3.pdf"];
const rows = files.map((name) => fixture.find((r) => r.filename === name));
assert.ok(rows.every(Boolean));
const report = { status: "running", mocked_responses: false, cloud_calls: 0,
  checks: {}, errors: [], forbidden_requests: [] };
const save = () => writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.setDefaultTimeout(45_000);
page.on("pageerror", (e) => report.errors.push(e.message));
await page.route("**/api/v1/**", async (route) => {
  const request = route.request(), url = new URL(request.url());
  if (["GET", "HEAD", "OPTIONS"].includes(request.method())) return route.continue();
  if (request.method() === "POST" && url.pathname.endsWith("/reviews/generate")
      && request.postDataJSON().generation_mode === "extractive"
      && JSON.stringify([...request.postDataJSON().document_ids].sort()) === JSON.stringify(rows.map((r) => r.document_id).sort())) {
    return route.continue();
  }
  report.forbidden_requests.push(url.pathname);
  return route.abort();
});

try {
  await page.goto("http://127.0.0.1:3344/#research-review");
  await page.getByLabel("工作区导航").getByRole("button", { name: /研究脉络/ }).click();
  await page.getByRole("button", { name: "证据综述", exact: true }).click();
  const section = page.locator("#research-review");
  await section.locator(".document-scope-picker summary").click();
  for (const row of rows) await section.locator(`label[data-document-id="${row.document_id}"] input`).check();
  await section.getByLabel("仅整理原文（不调用 LLM）").check();
  const response = page.waitForResponse((r) => r.url().endsWith("/reviews/generate") && r.status() === 200);
  await section.getByRole("button", { name: "整理综述", exact: true }).click();
  const review = await (await response).json();
  assert.ok(review.future_directions.every((d) => !/^Limitations\s*&\s*Future Work\.?$/i.test(d.text)));
  report.checks.bare_section_heading_not_recommended_as_direction = true;
  await writeFile(path.join(output, "served-review.json"), JSON.stringify(review, null, 2));
  const positive = review.future_directions.find((d) => d.source_document_id === rows[0].document_id
    && d.assessment?.excluded_from_recommendations);
  assert.ok(positive && positive.status === "evidence_addressed" && positive.exploration_score === 0);
  const excluded = section.locator("details.future-confirmed").filter({ hasText: "原计划已有完成证据" });
  await excluded.locator(":scope > summary").click();
  const card = excluded.locator(".future-card").filter({ hasText: "GPT-3 can be" }).first();
  await card.waitFor();
  report.checks.approved_goal_removed_from_open_recommendations = true;
  await card.locator(".future-decision-editor > summary").click();
  await card.getByText("已自动排除该原计划的开放推荐；依据保留在下方。").waitFor();
  await card.scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, "automatic-exclusion.png") });
  report.checks.full_goal_and_exact_proof_remain_visible = true;
  await card.getByRole("button", { name: /后续原文 · p\./ }).first().click();
  const dialog = page.getByRole("dialog");
  await waitForPaintedPdfPage(dialog, 6);
  assert.equal(await dialog.locator("#pdf-page-number").inputValue(), "6");
  await page.screenshot({ path: path.join(output, "later-source-page6.png") });
  report.checks.actual_later_pdf_page_rendered = true;
  await dialog.getByRole("button", { name: "关闭阅读器" }).click();
  const partial = review.future_directions.find((d) => d.source_document_id === rows[2].document_id
    && d.assessment?.status === "partial_progress");
  const unrelated = review.future_directions.find((d) => d.source_document_id === rows[4].document_id
    && d.assessment?.status === "insufficient");
  assert.ok(partial && partial.status !== "evidence_addressed" && !partial.assessment.excluded_from_recommendations);
  assert.ok(unrelated && unrelated.status !== "evidence_addressed" && !unrelated.assessment.excluded_from_recommendations);
  report.checks.partial_and_unrelated_goals_not_excluded = true;
  await page.setViewportSize({ width: 390, height: 844 });
  await card.scrollIntoViewIfNeeded();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  await page.screenshot({ path: path.join(output, "mobile.png") });
  report.checks.mobile_no_horizontal_overflow = true;
  assert.deepEqual(report.errors, []);
  assert.deepEqual(report.forbidden_requests, []);
  report.status = "passed";
} catch (error) {
  report.status = "failed";
  report.errors.push(String(error));
  await page.screenshot({ path: path.join(output, "failure.png") }).catch(() => {});
} finally {
  await save();
  await browser.close();
}
console.log(JSON.stringify(report));
if (report.status !== "passed") process.exitCode = 1;
