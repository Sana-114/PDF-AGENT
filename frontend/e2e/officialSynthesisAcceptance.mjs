// Actual isolated API and source frontend; extractive mode explicitly forbids cloud calls.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright-core";
import { waitForPaintedPdfPage } from "./pdfRenderReady.mjs";

const project = path.resolve("..");
const fixture = path.resolve(process.env.PDFAGENT_ACCEPTANCE_FIXTURE);
const output = path.resolve(process.env.PDFAGENT_SYNTHESIS_OUTPUT);
const api = new URL(process.env.PDFAGENT_ACCEPTANCE_API);
const base = new URL(process.env.PDFAGENT_DEMO_URL);
assert.ok([fixture, output].every(p => p.startsWith(path.join(project, "backend", "tmp") + path.sep)));
assert.ok([api, base].every(url => ["localhost", "127.0.0.1"].includes(url.hostname)));
assert.ok(Number(api.port) >= 8011 && Number(api.port) <= 8099);
const rows = JSON.parse(await readFile(path.join(fixture, "fixture.json"), "utf8"));
assert.equal(rows.length, 20);
await mkdir(output);
const browser = await chromium.launch({ channel: "msedge", headless: true, args: ["--no-proxy-server"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const report = { status: "running", scope: "source frontend and actual isolated extractive API, NOT cloud generation or Docker deployment", cloud_calls: 0, checks: [], errors: [] };
page.on("pageerror", error => report.errors.push(error.message));
await page.route("**/api/v1/**", async route => {
  const request = route.request(), url = new URL(request.url());
  assert.ok(request.method() === "GET" || (request.method() === "POST" && url.pathname === "/api/v1/reviews/generate"));
  if (request.method() === "POST") assert.equal(JSON.parse(request.postData()).generation_mode, "extractive");
  await route.continue({ url: new URL(url.pathname + url.search, api).href });
});
try {
  const documentsReady = page.waitForResponse(response =>
    new URL(response.url()).pathname === "/api/v1/documents" && response.status() === 200,
    { timeout: 90_000 });
  await page.goto(base.href, { waitUntil: "domcontentloaded" });
  assert.equal((await (await documentsReady).json()).items.length, 20);
  await page.getByRole("complementary", { name: "工作区导航" })
    .getByRole("button", { name: /研究脉络/ }).click({ timeout: 60_000 });
  await page.getByRole("button", { name: "证据综述", exact: true }).click();
  const section = page.locator("#research-review");
  await section.waitFor({ state: "visible" });
  await section.getByLabel("仅整理原文（不调用 LLM）").check();
  await section.locator(".document-scope-picker > summary").click();
  const scope = section.getByRole("group", { name: "综述文献范围" });
  for (const row of rows) await scope.locator(`label[data-document-id="${row.document_id}"] input`).check();
  const responsePromise = page.waitForResponse(response => response.url().includes("/reviews/generate") && response.request().method() === "POST");
  await section.getByRole("button", { name: "整理综述", exact: true }).click();
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  const review = await response.json();
  assert.equal(review.provider, "extractive");
  assert.deepEqual([...JSON.parse(response.request().postData()).document_ids].sort(), rows.map(r => r.document_id).sort());
  assert.equal(review.citation_relations.length, 4);
  if (process.env.PDFAGENT_FUTURE_EXPECTATIONS) {
    const expectedPath = path.resolve(process.env.PDFAGENT_FUTURE_EXPECTATIONS);
    assert.ok(expectedPath.startsWith(path.join(project, "backend", "tmp") + path.sep));
    const expected = JSON.parse(await readFile(expectedPath, "utf8"));
    assert.deepEqual(review.future_directions.map(d => d.direction_id).sort(),
      expected.future_directions.map(d => d.direction_id).sort());
    const sent = new Set(review.evidence.map(e => e.evidence_id));
    const local = new Set(review.future_work_evidence.map(e => e.evidence_id));
    assert.ok([...local].every(id => !sent.has(id)));
    assert.equal(await section.locator(".future-card").count(), expected.future_directions.length);
    report.checks.push({ name: "all budget-independent local direction cards have a separate evidence pool", passed: true, count: review.future_directions.length });
  }
  await writeFile(path.join(output, "review.json"), JSON.stringify(review, null, 2));
  const graphPanel = section.locator("details.review-claims");
  await graphPanel.locator("summary").click();
  assert.equal(await graphPanel.locator("section").count(), 4);
  assert.ok((await graphPanel.textContent()).includes("不证明方法继承或科研影响"));
  report.checks.push({ name: "explicit 20-paper scope and four page-anchored bibliography relations", passed: true });
  await graphPanel.screenshot({ path: path.join(output, "01-relations.png") });
  const relation = review.citation_relations[0];
  const anchor = review.evidence.find(e => e.evidence_id === relation.evidence_id);
  assert.equal(anchor.source_type, "reference");
  await graphPanel.getByRole("button", { name: /^参考条目/ }).first().click();
  const reader = page.getByRole("dialog", { name: /阅读/ });
  await waitForPaintedPdfPage(reader, anchor.page_number);
  await page.screenshot({ path: path.join(output, "02-reference-original.png") });
  report.checks.push({ name: "bibliography relation opens actual citing PDF at its reference page", passed: true, page: anchor.page_number, document_id: anchor.document_id });
  if (review.future_work_evidence?.length) {
    await reader.getByRole("button", { name: "关闭阅读器", exact: true }).click();
    const direction = review.future_directions[0];
    const source = review.future_work_evidence.find(e => e.evidence_id === direction.source_evidence_id);
    const card = section.locator(".future-card").filter({ hasText: direction.text }).first();
    await card.getByRole("button", { name: /^(Future Work 原文|原方向)/ }).click();
    await waitForPaintedPdfPage(page.getByRole("dialog", { name: /阅读/ }), source.page_number);
    await page.screenshot({ path: path.join(output, "03-local-direction-original.png") });
    report.checks.push({ name: "local-only direction evidence opens the actual source page", passed: true, page: source.page_number, document_id: source.document_id });
  }
  assert.deepEqual(report.errors, []);
  report.status = "passed";
} catch (error) {
  report.status = "failed";
  report.errors.push(error.message);
  await page.screenshot({ path: path.join(output, "failure.png") });
  process.exitCode = 1;
} finally {
  await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify(report));
}
