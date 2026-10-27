// Real current backend, isolated official fixtures; no mocked API responses.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright-core";
import { waitForPaintedPdfPage } from "./pdfRenderReady.mjs";

const project = path.resolve("..");
const fixture = path.resolve(process.env.PDFAGENT_ACCEPTANCE_FIXTURE || "../backend/tmp/official-functional-acceptance-2026-10-12/local-features-final");
const output = path.resolve(process.env.PDFAGENT_READER_ACCEPTANCE_OUTPUT || "../backend/tmp/official-functional-acceptance-2026-10-12/browser-reader-1");
const api = new URL(process.env.PDFAGENT_ACCEPTANCE_API || "http://127.0.0.1:8011");
const base = new URL(process.env.PDFAGENT_DEMO_URL || "http://localhost:3300");
const portableHtml = path.resolve(process.env.PDFAGENT_TRANSLATION_EXPORT_HTML || path.join(fixture, "..", "full-translation-1", "exports", "bilingual-1706.03762v1-zh.html"));
assert.ok(output.startsWith(path.join(project, "backend", "tmp") + path.sep));
assert.ok(portableHtml.startsWith(path.join(project, "backend", "tmp") + path.sep));
assert.ok([base, api].every(url => ["127.0.0.1", "localhost"].includes(url.hostname)));
assert.ok(!["8000", "80", "443"].includes(api.port), "Do not use the production API");
await mkdir(output); // Refuse to overwrite an earlier browser run.
const fixtures = JSON.parse(await readFile(path.join(fixture, "fixture.json"), "utf8"));
const transformer = fixtures.find(x => x.filename === "1706.03762v1.pdf");
const browser = await chromium.launch({ channel: "msedge", headless: true, args: ["--no-proxy-server"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.setDefaultTimeout(30000);
const report = { status: "running", cloud_calls: 0, scope: "real frontend + isolated actual backend; cached previously audited translation", frontend_url: base.href, isolated_api_url: api.href, checks: [], errors: [] };
page.on("pageerror", error => report.errors.push(error.message));
page.on("requestfailed", request => {
  if (request.url().includes("/api/v1/")) report.errors.push(`${request.method()} ${new URL(request.url()).pathname}: ${request.failure()?.errorText}`);
});
await page.route("**/api/v1/**", async route => {
  const url = new URL(route.request().url());
  // GET plus cached page translation only. The isolated backend uses an offline
  // provider, so even a missing cache cannot accidentally call a cloud model.
  if (route.request().method() !== "GET" && !/\/translations\/pages\/\d+$/.test(url.pathname)) {
    report.errors.push(`Prevented unexpected write/generation: ${url.pathname}`);
    return route.abort();
  }
  return route.continue({ url: new URL(url.pathname + url.search, api).href });
});
const snapshot = async name => page.screenshot({ path: path.join(output, `${name}.png`) });

try {
  await page.goto(base.href, { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作区导航").getByRole("button", { name: /文献库/ }).click();
  const card = page.locator(".document-card").filter({ hasText: "arXiv:1706.03762v1" }).first();
  await card.getByRole("button", { name: "在线阅读", exact: true }).click();
  const reader = page.getByRole("dialog", { name: /阅读/ });
  await waitForPaintedPdfPage(reader, 1);
  report.checks.push({ name: "original page rendered", passed: true });
  await snapshot("01-original-page");
  await reader.getByRole("button", { name: /3 Model Architecture/ }).click();
  await waitForPaintedPdfPage(reader, 2);
  assert.equal(await reader.locator("#pdf-page-number").inputValue(), "2");
  report.checks.push({ name: "outline jump to physical page 2", passed: true });
  // Pick an actual rendered citation marker, preserving its precise origin.
  const citation = reader.locator(".pdf-citation-link").first();
  await citation.waitFor();
  const label = await citation.getAttribute("data-reference-label");
  await citation.click();
  await reader.getByRole("button", { name: `返回正文 [${label}]`, exact: true }).waitFor();
  assert.match(await reader.locator(".pdf-evidence-status").innerText(), /参考文献/);
  const referencePage = Number(await reader.locator("#pdf-page-number").inputValue());
  await waitForPaintedPdfPage(reader, referencePage);
  await snapshot("02-reference-destination");
  await reader.getByRole("button", { name: `返回正文 [${label}]`, exact: true }).click();
  await waitForPaintedPdfPage(reader, 2);
  assert.match(await reader.locator(".pdf-evidence-status").innerText(), /正文引用/);
  report.checks.push({ name: "citation/reference exact-origin round trip", passed: true, label, source_page: 2, reference_page: referencePage });
  await reader.getByRole("button", { name: "中英", exact: true }).click();
  await reader.locator(".pdf-bilingual-segments article").first().waitFor();
  assert.match(await reader.locator(".pdf-translation-job").innerText(), /15\/15 页/);
  await snapshot("03-complete-bilingual-reader");
  report.checks.push({ name: "completed full translation visible", passed: true, pages: 15 });
  // At 100% this 792pt PDF can fit entirely in a 1080p viewport. A zero-range
  // scroll has no meaningful fraction; require actual overflow on both sides.
  for (let index = 0; index < 4; index++) await reader.getByRole("button", { name: "放大", exact: true }).click();
  await page.waitForFunction(() => [".pdf-reader-viewport", ".pdf-bilingual-pane"].every(selector => {
    const el = document.querySelector(selector); return el && el.scrollHeight - el.clientHeight > 50;
  }));
  const scrollRatios = async () => reader.evaluate(element => {
    const ratio = selector => { const el = element.querySelector(selector); return el.scrollTop / Math.max(el.scrollHeight - el.clientHeight, 1); };
    return [ratio(".pdf-reader-viewport"), ratio(".pdf-bilingual-pane")];
  });
  for (const [selector, fraction] of [[".pdf-reader-viewport", .55], [".pdf-bilingual-pane", .25]]) {
    await reader.locator(selector).evaluate((element, value) => { element.scrollTop = (element.scrollHeight - element.clientHeight) * value; }, fraction);
    await page.waitForFunction(({ left, right, wanted }) => {
      const a = document.querySelector(left), b = document.querySelector(right);
      const ratio = el => el.scrollTop / Math.max(el.scrollHeight - el.clientHeight, 1);
      return Math.abs(ratio(a) - wanted) < .04 && Math.abs(ratio(b) - wanted) < .04;
    }, { left: ".pdf-reader-viewport", right: ".pdf-bilingual-pane", wanted: fraction });
    const ratios = await scrollRatios();
    assert.ok(Math.abs(ratios[0] - fraction) < .05 && Math.abs(ratios[1] - fraction) < .05);
    report.checks.push({ name: `bidirectional ratio scroll: ${selector}`, passed: true, ratios });
  }
  const htmlLink = reader.getByRole("link", { name: "HTML", exact: true });
  await htmlLink.waitFor();
  report.checks.push({ name: "HTML/Markdown export actions visible", passed: true });
  await reader.getByRole("button", { name: "关闭阅读器" }).click();
  // Inspect the real portable output, not a mocked preview.
  await page.goto("file:///" + portableHtml.replaceAll("\\", "/"));
  assert.equal(await page.locator("section.page").count(), 15);
  await snapshot("04-portable-html");
  assert.deepEqual(report.errors, []);
  report.status = "passed";
} catch (error) {
  report.status = "failed";
  report.errors.push(error.message);
  await snapshot("failure");
  process.exitCode = 1;
} finally {
  await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify(report, null, 2));
}
