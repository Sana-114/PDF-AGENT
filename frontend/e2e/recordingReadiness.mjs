// Local-only recording smoke check: never invoke a configured external LLM.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright-core";

const baseUrl = process.env.PDFAGENT_DEMO_URL || "http://localhost:3300";
assert.ok(["localhost", "127.0.0.1", "[::1]"].includes(new URL(baseUrl).hostname), "This check supports local demo URLs only");
const outputDir = path.resolve("../docs/assets/recording");
const reportPath = path.resolve("../backend/tmp/stage76-recording-readiness.json");
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.setDefaultTimeout(30_000);
const errors = [];
const screenshots = [];
const report = { status: "failed", external_llm_called: false, viewport: { width: 1920, height: 1080 }, screenshots, errors };
page.on("pageerror", (error) => errors.push(error.message));
// Guard against accidental cloud calls through the application, including
// a forgotten default generation-mode toggle. Real API data is never mocked.
await page.route("**/api/v1/**", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  if (request.method() === "POST" && (
    /\/agent\/(?:ask|compare|translate|conversations)/.test(url.pathname) ||
    /\/writing\/(?:outline|translate)/.test(url.pathname) ||
    /\/translation/.test(url.pathname) ||
    /\/references\/resolve/.test(url.pathname) ||
    (/\/reviews\/generate$/.test(url.pathname) && request.postDataJSON()?.generation_mode !== "extractive")
  )) {
    errors.push(`Prevented external-generation action: ${url.pathname}`);
    return route.abort();
  }
  return route.continue();
});
await mkdir(outputDir, { recursive: true });
await mkdir(path.dirname(reportPath), { recursive: true });

async function capture(name) {
  await page.screenshot({ path: path.join(outputDir, `${name}.png`), fullPage: false });
  screenshots.push(`${name}.png`);
}

try {
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.getByLabel("工作区导航").getByRole("button", { name: /文献库/ }).click();
  const transformer = page.locator(".document-card").filter({ hasText: "arXiv:1706.03762v1" }).first();
  await transformer.waitFor();
  await capture("01-library");
  await transformer.getByRole("button", { name: "在线阅读", exact: true }).click();
  const reader = page.getByRole("dialog", { name: /阅读/ });
  await reader.locator(".react-pdf__Page__canvas").waitFor();
  assert.equal(await reader.locator(".pdf-reader-state.error").count(), 0);
  await capture("02-pdf-reader");
  await reader.getByRole("button", { name: "关闭阅读器" }).click();

  await page.getByLabel("工作区导航").getByRole("button", { name: /研究脉络/ }).click();
  await page.getByRole("button", { name: "计算图谱", exact: true }).click();
  await page.locator(".citation-summary").waitFor();
  await capture("03-citation-graph");
  await page.getByRole("button", { name: "证据综述", exact: true }).click();
  await page.getByRole("checkbox", { name: "仅整理原文（不调用 LLM）" }).check();
  const [reviewResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/reviews/generate") && response.request().method() === "POST"),
    page.getByRole("button", { name: "整理综述", exact: true }).click(),
  ]);
  assert.equal(reviewResponse.status(), 200);
  const review = await reviewResponse.json();
  assert.equal(review.provider, "extractive");
  await page.locator(".future-work-board").waitFor();
  report.future_direction_count = review.future_directions.length;
  await page.locator(".future-work-board").evaluate((element) => {
    window.scrollTo(0, window.scrollY + element.getBoundingClientRect().top - 24);
  });
  await capture("04-future-work");
  const unreviewed = review.future_directions[0];
  if (unreviewed && (!unreviewed.confirmation || unreviewed.confirmation.reason.startsWith("录制准备："))) {
    const source = review.evidence.find((item) => item.evidence_id === unreviewed.source_evidence_id);
    const decisionResponse = unreviewed.confirmation ? null : await page.request.put(`http://localhost:8000/api/v1/reviews/future-directions/${unreviewed.direction_id}/decision`, {
      data: { source: { document_id: source.document_id, page_number: source.page_number,
        block_ids: source.block_ids, quote: source.quote }, decision: "needs_review",
        reason: "录制准备：尚未完成研究判断，仅验证核验记录持久化。", progress: [] },
    });
    if (decisionResponse) assert.equal(decisionResponse.status(), 200);
    const restored = await page.request.post("http://localhost:8000/api/v1/reviews/generate", {
      data: { document_ids: review.papers.map((paper) => paper.document_id), generation_mode: "extractive" },
    });
    const restoredReview = await restored.json();
    assert.equal(restoredReview.future_directions.find((item) => item.direction_id === unreviewed.direction_id).confirmation.decision, "needs_review");
    report.confirmation_persistence = "passed (needs_review only, not a scientific resolution claim)";
  }

  await page.getByLabel("工作区导航").getByRole("button", { name: /研究产出/ }).click();
  await page.getByRole("button", { name: "数据作图", exact: true }).click();
  await page.locator('.csv-upload-form input[type="file"]').setInputFiles({ name: "demo-methods.csv", mimeType: "text/csv",
    buffer: Buffer.from("method,accuracy,f1,recall,precision\nBaseline,0.78,0.75,0.74,0.76\nHybrid,0.86,0.84,0.83,0.85\nReranked,0.91,0.89,0.88,0.90\n") });
  await page.locator(".csv-upload-form select").selectOption("radar3d");
  await page.getByRole("button", { name: "绘制图表", exact: true }).click();
  await page.locator(".scientific-chart-svg").waitFor();
  await capture("05-data-visualization");
  await page.getByRole("button", { name: "架构图", exact: true }).click();
  await page.getByRole("button", { name: "整理四种脚本", exact: true }).click();
  await page.locator(".architecture-preview-svg").waitFor();
  await page.locator(".architecture-result-grid").evaluate((element) => {
    window.scrollTo(0, window.scrollY + element.getBoundingClientRect().top - 24);
  });
  await capture("06-architecture-diagram");
  assert.deepEqual(errors, []);
  report.status = "passed";
} catch (error) {
  errors.push(error.message);
  await page.screenshot({ path: path.join(outputDir, "failure.png"), fullPage: false });
  process.exitCode = 1;
} finally {
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  await browser.close();
  console.log(JSON.stringify(report, null, 2));
}
