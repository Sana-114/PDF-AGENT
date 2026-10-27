// Short actual-browser capture for encoding QA; not the final competition film.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright-core";
import { waitForPaintedPdfPage } from "./pdfRenderReady.mjs";

const root = path.resolve("../backend/tmp/official-runtime-resume-2026-10-25/recording-smoke");
const videoDir = path.join(root, "raw-video");
await mkdir(videoDir); // Never overwrite an existing take.
const rows = JSON.parse(await readFile("../backend/tmp/official-recording-release-2026-10-24/data/fixture.json", "utf8"));
const browser = await chromium.launch({ channel: "msedge", headless: true, args: ["--no-proxy-server"] });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 },
  recordVideo: { dir: videoDir, size: { width: 1920, height: 1080 } } });
const page = await context.newPage();
const report = { status: "running", kind: "audiovisual_smoke_not_final_demo", mocked_responses: false, cloud_calls: 0, errors: [] };
page.on("pageerror", error => report.errors.push(error.message));
await page.route("**/api/v1/**", async route => {
  const request = route.request();
  assert.ok(request.method() === "GET" || (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/citation-graph"));
  await route.continue();
});
try {
  await page.goto("http://127.0.0.1:3344", { waitUntil: "networkidle" });
  await page.waitForTimeout(4000);
  await page.getByLabel("工作区导航").getByRole("button", { name: /文献库/ }).click();
  await page.waitForTimeout(4000);
  await page.getByLabel("工作区导航").getByRole("button", { name: /研究脉络/ }).click();
  const section = page.locator("#citation-graph");
  await section.locator(".document-scope-picker > summary").click();
  for (const row of rows) await section.locator(`label[data-document-id="${row.document_id}"] input`).check();
  await section.locator(".document-scope-picker > summary").click();
  await section.getByRole("button", { name: "计算图谱", exact: true }).click();
  const graph = section.getByRole("img", { name: "局域引用关系图" });
  await graph.waitFor();
  await graph.scrollIntoViewIfNeeded();
  await page.waitForTimeout(6000);
  await graph.getByRole("button", { name: /Attention Is All You Need/ }).click();
  await section.getByRole("button", { name: "打开原文", exact: true }).click();
  const reader = page.getByRole("dialog", { name: /阅读/ });
  await waitForPaintedPdfPage(reader, 1);
  await page.waitForTimeout(8000);
  await page.screenshot({ path: path.join(root, "last-frame.png") });
  assert.deepEqual(report.errors, []);
  report.status = "captured_pending_media_review";
} catch (error) {
  report.status = "failed";
  report.errors.push(error.message);
  process.exitCode = 1;
} finally {
  await context.close();
  report.video = await page.video().path();
  await browser.close();
  await writeFile(path.join(root, "capture-report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
}
