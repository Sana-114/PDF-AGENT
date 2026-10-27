// Writes/deletes only in a freshly created isolated version fixture.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright-core";

const project = path.resolve("..");
const corpus = path.resolve(process.env.PDFAGENT_OFFICIAL_CORPUS);
const output = path.resolve(process.env.PDFAGENT_VERSION_OUTPUT);
assert.ok(output.startsWith(path.join(project, "backend", "tmp") + path.sep));
const api = new URL(process.env.PDFAGENT_ACCEPTANCE_API);
const base = new URL(process.env.PDFAGENT_DEMO_URL);
assert.ok([api, base].every(url => ["localhost", "127.0.0.1"].includes(url.hostname)));
assert.ok(Number(api.port) >= 8011 && Number(api.port) <= 8099);
const buffers = { 7: await readFile(path.join(corpus, "1706.03762v7.pdf")), 1: await readFile(path.join(corpus, "1706.03762v1.pdf")) };
const hashes = Object.fromEntries(Object.entries(buffers).map(([version, buffer]) => [version, createHash("sha256").update(buffer).digest("hex")]));
const library = async () => {
  const response = await fetch(new URL("/api/v1/documents", api));
  assert.equal(response.status, 200);
  return response.json();
};
assert.equal((await library()).total, 0, "Refuse any existing library, including earlier failed runs");
await mkdir(output);
const browser = await chromium.launch({ channel: "msedge", headless: true, args: ["--no-proxy-server"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.setDefaultTimeout(30000);
const report = { status: "running", scope: "Docker frontend/API; real renamed v7/v1 uploads; only isolated fixture mutations", cloud_calls: 0, checks: [], uploads: [], errors: [] };
page.on("pageerror", error => report.errors.push(error.message));
page.on("dialog", dialog => dialog.accept());
await page.route("**/api/v1/**", async route => {
  const request = route.request(), url = new URL(request.url());
  assert.ok(request.method() === "GET" || (request.method() === "POST" && (url.pathname === "/api/v1/documents" || /\/documents\/[^/]+\/duplicates\/resolve$/.test(url.pathname))));
  await route.continue({ url: new URL(url.pathname + url.search, api).href });
});
const upload = async (version, name) => {
  const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/documents" && response.request().method() === "POST", { timeout: 120000 });
  await page.locator('.dropzone input[type="file"]').setInputFiles({ name, mimeType: "application/pdf", buffer: buffers[version] });
  const response = await responsePromise;
  assert.equal(response.status(), 202);
  const payload = await response.json();
  const uploadRecord = { filename: name, source_version: version, document: payload.document, exact_duplicate: payload.exact_duplicate, observed_statuses: [payload.document.status] };
  report.uploads.push(uploadRecord);
  await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
  // Production uses an external Worker: HTTP 202 legitimately returns queued.
  // Poll this exact document; never reupload/reparse or convert failure to success.
  const deadline = Date.now() + 600_000;
  while (!["ready", "failed"].includes(payload.document.status) && Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 2000));
    const progress = await fetch(new URL(`/api/v1/documents/${payload.document.id}`, api));
    assert.equal(progress.status, 200);
    payload.document = await progress.json();
    if (uploadRecord.observed_statuses.at(-1) !== payload.document.status) {
      uploadRecord.observed_statuses.push(payload.document.status);
      await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
    }
  }
  uploadRecord.completed_document = payload.document;
  await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
  assert.equal(payload.document.status, "ready", payload.document.error_message || "Parsing was not complete");
  assert.equal(payload.document.sha256, hashes[version]);
  assert.equal(payload.document.arxiv_id, "1706.03762");
  assert.equal(payload.document.arxiv_version, version, "Version must be extracted from PDF, not opaque filename");
  return payload;
};
const resolve = async (label, action) => {
  const responsePromise = page.waitForResponse(response => response.url().includes("/duplicates/resolve") && response.request().method() === "POST");
  await page.getByRole("button", { name: label, exact: true }).click();
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  const payload = await response.json();
  assert.equal(payload.action, action);
  return payload;
};
try {
  await page.goto(base.href, { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作区导航").getByRole("button", { name: /文献库/ }).click();
  const latest = await upload(7, "latest-upload.pdf");
  const earlier = await upload(1, "earlier-upload.pdf");
  assert.equal(earlier.document.duplicate_of_id, latest.document.id);
  await page.locator(".version-warning").waitFor();
  assert.match(await page.locator(".version-warning").innerText(), /库中已有更新的 arXiv v7/);
  report.checks.push({ name: "renamed v7 then v1 content conflict and newer-version advice", passed: true, score: earlier.document.duplicate_score });
  const diffResponse = page.waitForResponse(response => response.url().includes("/duplicates/diff"));
  await page.getByRole("button", { name: "查看差异", exact: true }).click();
  const diff = await diffResponse;
  assert.equal(diff.status(), 200);
  const diffPayload = await diff.json();
  assert.ok(diffPayload.content_overlap > .3);
  await page.locator(".version-difference").waitFor();
  await page.screenshot({ path: path.join(output, "01-version-warning-diff.png") });
  report.checks.push({ name: "evidence-linked version diff displayed", passed: true, content_overlap: diffPayload.content_overlap });
  let decision = await resolve("保留库中版本", "keep_existing");
  assert.equal(decision.kept_document.sha256, hashes[7]);
  assert.equal(decision.removed_document_id, earlier.document.id);
  assert.equal((await library()).total, 1);
  report.checks.push({ name: "keep existing retains v7 and removes only isolated v1", passed: true });
  await page.getByRole("button", { name: "保留库中版本", exact: true }).waitFor({ state: "detached" });
  await upload(1, "another-earlier-upload.pdf");
  decision = await resolve("用当前版本替换", "replace_existing");
  assert.equal(decision.kept_document.sha256, hashes[1]);
  assert.equal(decision.removed_document_id, latest.document.id);
  assert.equal((await library()).total, 1);
  report.checks.push({ name: "replace existing respects user choice and retains v1", passed: true });
  await page.getByRole("button", { name: "用当前版本替换", exact: true }).waitFor({ state: "detached" });
  await upload(7, "latest-again.pdf");
  await resolve("两版并存", "keep_both");
  assert.equal((await library()).total, 2);
  report.checks.push({ name: "keep both retains two distinct official version hashes", passed: true });
  await page.getByRole("button", { name: "两版并存", exact: true }).waitFor({ state: "detached" });
  const duplicate = await upload(1, "identical-content-different-name.pdf");
  assert.equal(duplicate.exact_duplicate, true);
  assert.equal((await library()).total, 2);
  report.checks.push({ name: "exact byte duplicate under a different name is not reinserted", passed: true });
  await page.screenshot({ path: path.join(output, "02-final-two-versions.png") });
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
  console.log(JSON.stringify({ status: report.status, cloud_calls: report.cloud_calls, checks: report.checks, errors: report.errors }, null, 2));
}
