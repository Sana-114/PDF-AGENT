/** Real, read-only reader acceptance against a pinned isolated official fixture. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";
import { waitForPaintedPdfPage } from "./pdfRenderReady.mjs";

const args = Object.fromEntries(process.argv.slice(2).map((value, index, all) =>
  value.startsWith("--") ? [value.slice(2), all[index + 1]] : null,
).filter(Boolean));
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const temp = path.join(repo, "backend/tmp");
const inside = (value) => {
  const result = path.resolve(value);
  assert.ok(path.relative(temp, result) && !path.relative(temp, result).startsWith("..")
    && !path.isAbsolute(path.relative(temp, result)), "Use a dedicated backend/tmp directory");
  return result;
};
const fixture = inside(args.fixture || "");
const output = inside(args.output || "");
assert.notEqual(output, fixture);
const web = new URL(args.url || "http://127.0.0.1:3238");
const api = new URL(args.api || "http://127.0.0.1:8038");
for (const [url, port] of [[web, "3238"], [api, "8038"]]) {
  assert.ok(url.protocol === "http:" && ["127.0.0.1", "localhost"].includes(url.hostname)
    && url.port === port && !url.username && !url.password && !url.search && !url.hash);
}
const source = JSON.parse(await readFile(path.join(fixture, "report.json"), "utf8"));
const astBytes = await readFile(path.join(fixture, "fresh-parsed.json"));
const ast = JSON.parse(astBytes);
assert.equal(source.status, "passed_scoped_checks");
assert.equal(source.source_sha256, "8033c3e611aaa120cb7294085ebd6ee297ffae9ec02393b1c4f1593d7e6ee065");
assert.equal(createHash("sha256").update(await readFile(path.join(fixture, "uploads", source.document_id + ".pdf"))).digest("hex"), source.source_sha256);
await mkdir(output, { recursive: false }); // Never overwrite a previous run.
const report = { status: "running", document_id: source.document_id,
  source_sha256: source.source_sha256, ast_sha256: createHash("sha256").update(astBytes).digest("hex"),
  scope: "Current source frontend/API, official BLOOM and page-scoped contributor navigation; not all name accuracy or a full corpus browser test",
  checks: {}, sources: [], page_errors: [], forbidden_requests: [], people_requests: 0,
  injected_error_responses: 0, cloud_calls: 0, production_library_modified: false };
report.injected_empty_state_responses = 0;
const browser = await chromium.launch({ channel: process.env.PDFAGENT_BROWSER_CHANNEL || "msedge", headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(45_000);
page.on("pageerror", (error) => report.page_errors.push(error.message));
await context.route("**/api/v1/**", async (route) => {
  if (!["GET", "HEAD", "OPTIONS"].includes(route.request().method())) {
    report.forbidden_requests.push(new URL(route.request().url()).pathname);
    return route.abort();
  }
  if (new URL(route.request().url()).pathname.endsWith("/people")) report.people_requests++;
  return route.continue();
});

async function sourceJump(dialog, item, label) {
  await dialog.locator(`.pdf-people-sources button[data-source-page="${item.page_number}"][data-source-block-id="${item.block_id}"]`).click();
  assert.equal(await dialog.locator("#pdf-page-number").inputValue(), String(item.page_number));
  await waitForPaintedPdfPage(dialog, item.page_number);
  const mark = dialog.getByLabel(`署名与贡献 ${label} 的原文位置`, { exact: true });
  await mark.waitFor({ state: "visible" });
  assert.equal(await mark.getAttribute("title"), item.text);
  const pageAst = ast.pages.find((p) => p.page_number === item.page_number);
  const block = pageAst.blocks.find((b) => b.block_id === item.block_id);
  assert.deepEqual(block.bbox, item.bbox);
  assert.equal(block.text, item.text);
  const actual = await mark.evaluate((element) => ({
    left: parseFloat(element.style.left), top: parseFloat(element.style.top),
    width: parseFloat(element.style.width), height: parseFloat(element.style.height),
  }));
  const expected = { left: item.bbox[0] / pageAst.width * 100,
    top: item.bbox[1] / pageAst.height * 100,
    width: (item.bbox[2] - item.bbox[0]) / pageAst.width * 100,
    height: (item.bbox[3] - item.bbox[1]) / pageAst.height * 100 };
  for (const key of Object.keys(expected)) assert.ok(Math.abs(actual[key] - expected[key]) < 0.001);
  const visible = await mark.evaluate((element) => {
    const r = element.getBoundingClientRect();
    const v = element.closest(".pdf-reader-viewport").getBoundingClientRect();
    return Math.min(r.bottom, v.bottom) > Math.max(r.top, v.top)
      && Math.min(r.right, v.right) > Math.max(r.left, v.left);
  });
  assert.equal(visible, true, "Source highlight must intersect the visible PDF viewport");
  assert.match(await dialog.locator(".pdf-evidence-status").innerText(), /已定位署名与贡献原文/);
  report.sources.push({ role: label, page: item.page_number, block_id: item.block_id, painted_canvas: true, coordinates_match: true, visible });
}

try {
  const response = await context.request.get(`${api.origin}/api/v1/documents/${source.document_id}/people`);
  assert.equal(response.status(), 200);
  const data = await response.json();
  await writeFile(path.join(output, "api-people.json"), JSON.stringify(data, null, 2));
  assert.deepEqual(data.authors, ["BigScience Workshop"]);
  assert.deepEqual(data.affiliations, []);
  assert.equal(data.contributor_status, "available");
  assert.equal(data.contributor_groups.length, 10);
  assert.equal(data.contributor_groups.flatMap((g) => g.sources).length, 12);
  assert.ok(!("pages" in data) && (await response.body()).length < astBytes.length / 10);
  report.checks.compact_api_and_distinct_collective_credit = true;
  const range = await context.request.get(`${api.origin}/api/v1/documents/${source.document_id}/file`, { headers: { Range: "bytes=0-1023" } });
  assert.equal(range.status(), 206);
  assert.equal((await range.body()).subarray(0, 5).toString(), "%PDF-");
  report.checks.real_pdf_range = true;
  await page.goto(`${web.origin}/#library`, { waitUntil: "domcontentloaded" });
  const card = page.locator(".document-card").filter({ hasText: ast.title });
  await card.getByRole("button", { name: "在线阅读", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: /阅读/ });
  await waitForPaintedPdfPage(dialog, 1);
  assert.equal(report.people_requests, 0, "Do not fetch people until its tab is opened");
  await dialog.getByRole("tab", { name: "署名与贡献" }).click();
  const panel = dialog.locator(".pdf-people-panel");
  await panel.getByRole("heading", { name: "论文署名", exact: true }).waitFor();
  assert.equal(await panel.locator("details").count(), 10);
  assert.equal(await panel.locator("details[open]").count(), 0);
  assert.match(await panel.getByRole("region", { name: "论文署名", exact: true }).innerText(), /BigScience Workshop/);
  assert.match(await panel.innerText(), /不是全局作者顺序/);
  assert.match(await panel.innerText(), /重音字符沿用 PDF 文本层/);
  report.checks.lazy_loading_collapsed_groups_and_scope_notice = true;
  await sourceJump(dialog, data.author_sources[0], "论文署名");
  for (const group of data.contributor_groups) {
    // Exact attribute avoids confusing "Engineering" with "Prompt Engineering".
    const exact = panel.locator(`details[data-contributor-role="${group.role}"]`);
    await exact.locator("summary").click();
    assert.equal(await exact.locator(".pdf-contributor-members").innerText(), group.members.join("；"));
    for (const item of group.sources) await sourceJump(dialog, item, group.role);
    if (["Major Contributors", "Prompt Engineering", "Applications"].includes(group.role)) {
      await page.screenshot({ path: path.join(output, `${group.role.replaceAll(" ", "-")}.png`) });
    }
    await exact.locator("summary").click();
  }
  report.checks.all_twelve_role_segments_match_raw_blocks = true;
  report.checks.members_displayed_without_global_ordinals = true;
  // Explicitly inject one transport failure only to check the error/retry UI.
  await dialog.getByRole("tab", { name: /^目录/ }).click();
  await page.route(`**/documents/${source.document_id}/people`, async (route) => {
    report.injected_error_responses++;
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "验收注入：署名接口暂不可用" }) });
  }, { times: 1 });
  await dialog.getByRole("tab", { name: "署名与贡献" }).click();
  await dialog.getByRole("alert").getByText(/验收注入/).waitFor();
  await dialog.getByRole("button", { name: "重试", exact: true }).click();
  await dialog.locator(".pdf-people-panel details").first().waitFor();
  report.checks.explicit_error_and_manual_retry = true;
  // Synthetic API states are separate from the real official-PDF source checks.
  for (const [status, notice] of [["legacy", "旧解析结果未包含贡献分组"],
    ["not_detected", "未检测到独立的贡献分组"], ["unverified", "部分贡献分组的原文来源不完整"]]) {
    await dialog.getByRole("tab", { name: /^目录/ }).click();
    await page.route(`**/documents/${source.document_id}/people`, async (route) => {
      report.injected_empty_state_responses++;
      await route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ ...data, contributor_groups: [], contributor_status: status }) });
    }, { times: 1 });
    await dialog.getByRole("tab", { name: "署名与贡献" }).click();
    await dialog.locator(".pdf-people-panel").getByText(notice, { exact: false }).waitFor();
    assert.equal(await dialog.locator(".pdf-people-panel details").count(), 0);
  }
  report.checks.synthetic_legacy_not_detected_unverified_states = true;
  await dialog.getByRole("tab", { name: /^目录/ }).click();
  await dialog.getByRole("tab", { name: "署名与贡献" }).click();
  await dialog.locator(".pdf-people-panel details").first().waitFor();
  // Small-screen navigation must close its overlay and expose the chosen page.
  await page.setViewportSize({ width: 390, height: 844 });
  const applications = dialog.locator('details[data-contributor-role="Applications"]');
  await applications.locator("summary").click();
  const continuation = data.contributor_groups.find((group) => group.role === "Applications").sources[1];
  await sourceJump(dialog, continuation, "Applications");
  assert.equal(await dialog.getByRole("button", { name: /导航/ }).getAttribute("aria-expanded"), "false");
  await page.waitForFunction(() => {
    const aside = document.querySelector(".pdf-outline").getBoundingClientRect();
    const body = document.querySelector(".pdf-reader-body").getBoundingClientRect();
    return aside.right <= body.left + 1;
  }, null, { timeout: 5000 });
  const highlightExposed = await dialog.locator(".pdf-evidence-highlight.people").evaluate((element) => {
    const r = element.getBoundingClientRect();
    const v = element.closest(".pdf-reader-viewport").getBoundingClientRect();
    const x = (Math.max(r.left, v.left) + Math.min(r.right, v.right)) / 2;
    const y = (Math.max(r.top, v.top) + Math.min(r.bottom, v.bottom)) / 2;
    return Boolean(document.elementFromPoint(x, y)?.closest(".pdf-reader-viewport"));
  });
  assert.equal(highlightExposed, true, "Collapsed sidebar must not obscure PDF source pixels");
  await page.screenshot({ path: path.join(output, "mobile-Applications.png") });
  report.checks.mobile_source_navigation_closes_sidebar = true;
  await dialog.getByRole("button", { name: /导航/ }).click();
  const tab = dialog.getByRole("tab", { name: "署名与贡献" });
  await page.waitForFunction(() => {
    const aside = document.querySelector(".pdf-outline").getBoundingClientRect();
    const body = document.querySelector(".pdf-reader-body").getBoundingClientRect();
    return Math.abs(aside.left - body.left) <= 1;
  }, null, { timeout: 5000 });
  assert.equal(await tab.evaluate((element) => {
    const r = element.getBoundingClientRect();
    return Boolean(document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2)?.closest(".pdf-outline"));
  }), true);
  report.checks.mobile_sidebar_paints_above_pdf_highlight = true;
  assert.deepEqual(report.forbidden_requests, []);
  assert.deepEqual(report.page_errors, []);
  report.checks.no_generation_or_mutation_requests = true;
  report.status = "passed_scoped_checks";
} catch (error) {
  report.status = "failed";
  report.error = { name: error.name, message: error.message, stack: error.stack };
  await page.screenshot({ path: path.join(output, "failure.png") }).catch(() => {});
} finally {
  await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ status: report.status, checks: report.checks, source_jumps: report.sources.length, error: report.error?.message }));
process.exitCode = report.status === "passed_scoped_checks" ? 0 : 1;
