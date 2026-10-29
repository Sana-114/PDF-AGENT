// Actual public PDFs and current HTTP API, no mocked responses or model calls.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright-core";
import { waitForPaintedPdfPage } from "./pdfRenderReady.mjs";

const project = path.resolve("..");
const fixture = path.resolve(process.env.PDFAGENT_ACCEPTANCE_FIXTURE || "../backend/tmp/author-year-2026-10-29/fixture");
const output = path.resolve(process.env.PDFAGENT_REFERENCE_OUTPUT || "../backend/tmp/author-year-2026-10-29/browser-1");
assert.ok(output.startsWith(path.join(project, "backend", "tmp") + path.sep));
const base = "http://127.0.0.1:3304", api = "http://127.0.0.1:8027";
await mkdir(output);
const rows = JSON.parse(await readFile(path.join(fixture, "fixture.json"), "utf8"));
const browser = await chromium.launch({ channel: "msedge", headless: true, args: ["--no-proxy-server"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.setDefaultTimeout(45000);
const report = { status: "running", cloud_calls: 0, checks: [], errors: [] };
page.on("pageerror", error => report.errors.push(error.message));
await page.route("**/api/v1/**", route => {
  const url = new URL(route.request().url());
  if (route.request().method() !== "GET") return route.abort();
  return route.continue({ url: api + url.pathname + url.search });
});
try {
  await page.goto(base, { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作区导航").getByRole("button", { name: /文献库/ }).click();
  const cases = [
    ["1607.06450v1.pdf", "Krizhevsky et al., 2012", "Imagenet"],
    ["1910.10683v4.pdf", "Mikolov et al., 2013b", "Distributed representations"],
    ["2002.04745v2.pdf", "Ba et al., 2016", "Layer normaliza"],
    ["1706.03762v1.pdf", null, null],
  ];
  for (const [filename, citation, title] of cases) {
    const row = rows.find(r => r.filename === filename);
    const response = await page.request.get(`${api}/api/v1/documents/${row.document_id}/references`);
    assert.equal(response.status(), 200);
    const data = await response.json();
    const mention = data.mentions.find(m => citation
      ? m.citation_text?.replace(/\s+/g, " ") === citation
      : /^\d+$/.test(m.label) && m.page_number === 2 && m.citation_text === `[${m.label}]`);
    assert.ok(mention, filename);
    const reference = data.items.find(r => r.label === mention.label);
    if (title) assert.ok(reference.text.toLowerCase().includes(title.toLowerCase()));
    await page.locator(".document-card").filter({ hasText: `arXiv:${filename.replace(".pdf", "")}` })
      .first().getByRole("button", { name: "在线阅读", exact: true }).click();
    const reader = page.getByRole("dialog", { name: /阅读/ });
    await waitForPaintedPdfPage(reader, 1);
    if (mention.page_number !== 1) {
      await reader.locator("#pdf-page-number").fill(String(mention.page_number));
      await reader.locator("#pdf-page-number").press("Enter");
      await waitForPaintedPdfPage(reader, mention.page_number);
    }
    const marker = reader.locator(`.pdf-citation-link[data-reference-label="${mention.label}"]`).first();
    await marker.waitFor();
    await marker.scrollIntoViewIfNeeded();
    const box = await marker.boundingBox();
    assert.ok(box);
    // Native PDF annotations can cover this text. Use an actual pointer click
    // at the citation; do not force-dispatch directly to the hidden text mark.
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await waitForPaintedPdfPage(reader, reference.page_number);
    await reader.locator(".pdf-evidence-status").filter({ hasText: "已定位参考文献条目" }).waitFor();
    assert.match(await reader.locator(".pdf-evidence-status").innerText(), /参考文献/);
    await page.screenshot({ path: path.join(output, `${filename}-reference.png`) });
    await reader.getByRole("button", { name: /^返回正文/ }).click();
    await waitForPaintedPdfPage(reader, mention.page_number);
    await reader.locator(".pdf-evidence-status").filter({ hasText: "已定位正文引用" }).waitFor();
    assert.match(await reader.locator(".pdf-evidence-status").innerText(), /正文引用/);
    await page.screenshot({ path: path.join(output, `${filename}-return.png`) });
    // Sidebar links support bibliography -> body without relying on an inline origin.
    await reader.locator(".pdf-reference-entry").filter({ hasText: reference.text.slice(0, 35) }).first().click();
    await reader.getByRole("button", { name: "下一处引用", exact: true }).click();
    const nextPage = Number(await reader.locator("#pdf-page-number").inputValue());
    await waitForPaintedPdfPage(reader, nextPage);
    await reader.locator(".pdf-evidence-status").filter({ hasText: "已定位正文引用" }).waitFor();
    assert.match(await reader.locator(".pdf-evidence-status").innerText(), /正文引用/);
    report.checks.push({ filename, citation, label: mention.label, passed: true,
      original_page: mention.page_number, reference_page: reference.page_number,
      reference_id: reference.reference_id, source_sha256: row.source_sha256,
      checks: ["real PDF rendered", "inline to bibliography", "return to body", "sidebar to body"] });
    await page.keyboard.press("Escape");
  }
  assert.deepEqual(report.errors, []);
  report.status = "passed";
} catch (error) {
  report.status = "failed"; report.errors.push(String(error));
  await page.screenshot({ path: path.join(output, "failure.png") });
  process.exitCode = 1;
} finally {
  await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify(report));
}
