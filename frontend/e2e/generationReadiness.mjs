// Local real-UI gate. Cloud calls require an explicit flag and are limited to
// one synthetic translation and two SHA-pinned public papers per generation task.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { waitForPaintedPdfPage } from "./pdfRenderReady.mjs";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const argv = process.argv.slice(2);
const value = (name, fallback) => argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback;
const baseUrl = value("--url", "http://localhost:3300");
const apiUrl = value("--api", "http://localhost:8000/api/v1");
for (const url of [baseUrl, apiUrl]) assert.ok(["localhost", "127.0.0.1", "[::1]"].includes(new URL(url).hostname));
const cloudAllowed = argv.includes("--allow-cloud");
const selectedCase = value("--case", "all");
assert.ok(["all", "translation", "review", "writing"].includes(selectedCase));
const output = path.resolve(value("--output", path.join(repo, `backend/tmp/generation-readiness/run-${Date.now()}.json`)));
const screenshotDir = path.join(path.dirname(output), path.basename(output, ".json"));
await mkdir(screenshotDir, { recursive: true });

export const sourceText = "本合成示例讨论检索增强生成方法。该方法可能降低科研问答中的无依据陈述，但我们尚未开展性能实验，不报告准确率或 BLEU 分数。\n\n公式 $y=x^2$ 与代码 `beta_2 = 0.98` 保持不变，引用标记 [11] 仅用于完整性测试。";
const idea = "Compare evidence-supported architectural choices in the original Transformer and BERT; outline a proposed study without inventing results or new references.";
const browser = await chromium.launch({ channel: process.platform === "win32" ? "msedge" : "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, acceptDownloads: true });
const page = await context.newPage();
page.setDefaultTimeout(30_000);
const report = { evaluation_kind: "real_browser_generation", timestamp: new Date().toISOString(), status: "failed", cloud_authorized: cloudAllowed, cases: [], request_counts: {}, page_errors: [], blocked_requests: [], screenshot_dir: screenshotDir };
page.on("pageerror", (error) => report.page_errors.push(error.message));
let allowedIds = [];

await page.route("**/api/v1/**", async (route) => {
  const request = route.request();
  if (request.method() !== "POST") return route.continue();
  const endpoint = new URL(request.url()).pathname.split("/api/v1")[1];
  const body = request.postDataJSON();
  const supported = ["/writing/translate", "/reviews/generate", "/writing/outline"];
  let allowed = cloudAllowed && supported.includes(endpoint) && !report.request_counts[endpoint];
  if (endpoint === "/writing/translate") allowed &&= body.text === sourceText && body.source_language === "zh" && body.target_language === "en";
  else allowed &&= Array.isArray(body.document_ids) && allowedIds.length === 2 && body.document_ids.length === 2 && body.document_ids.every((id) => allowedIds.includes(id));
  if (endpoint === "/writing/outline") allowed &&= body.idea === idea;
  if (endpoint === "/reviews/generate") allowed &&= body.generation_mode === "configured";
  if (!allowed) {
    report.blocked_requests.push(endpoint);
    return route.abort();
  }
  report.request_counts[endpoint] = 1;
  return route.continue(); // Never substitute API results or prerecorded answers.
});

async function capture(name) {
  const filename = path.join(screenshotDir, `${name}.png`);
  await page.screenshot({ path: filename, fullPage: false });
  return filename;
}

async function chooseScope(section, label) {
  const details = page.locator(`${section} .document-scope-picker`);
  await details.locator("summary").click();
  const scope = page.getByRole("group", { name: label });
  assert.equal(await scope.locator("input:checked").count(), 0, "No documents may be selected implicitly");
  for (const id of allowedIds) await scope.locator(`[data-document-id="${id}"] input`).check();
  assert.equal(await scope.locator("input:checked").count(), 2);
  await details.locator("summary").click();
}

async function submit(endpoint, button) {
  const [response] = await Promise.all([
    page.waitForResponse((item) => item.url().endsWith(endpoint) && item.request().method() === "POST", { timeout: 180_000 }),
    button.click(),
  ]);
  const payload = await response.json();
  // Persist the real response before evaluating it; failures are not overwritten.
  report.cases.push({ case_id: endpoint, http_status: response.status(), response: payload, status: "pending" });
  await writeFile(output, JSON.stringify(report, null, 2));
  assert.equal(response.status(), 200, `Unexpected status for ${endpoint}`);
  assert.equal(payload.provider, "deepseek", "Extractive fallback is not successful model generation");
  assert.equal(payload.model, "deepseek-flash");
  return [payload, report.cases.at(-1)];
}

function checkGrounding(payload) {
  assert.equal(payload.insufficient_evidence, false);
  assert.ok(payload.claims.length);
  const evidence = new Map(payload.evidence.map((item) => [item.evidence_id, item]));
  for (const item of payload.evidence) assert.ok(allowedIds.includes(item.document_id));
  for (const claim of payload.claims) {
    assert.ok(claim.evidence_ids.length);
    for (const id of claim.evidence_ids) assert.ok(evidence.has(id));
  }
  return evidence;
}

async function checkReader(evidence, parentSelector) {
  const parent = page.locator(parentSelector);
  const button = parent.getByRole("button", { name: new RegExp(`^${evidence.evidence_id} · p\\.${evidence.page_number}$`) }).first();
  await button.click();
  const reader = page.getByRole("dialog", { name: /阅读/ });
  await waitForPaintedPdfPage(reader, evidence.page_number);
  assert.ok(await reader.locator(".pdf-evidence-highlight").count(), "Evidence overlay must exist");
  const screenshot = await capture(`source-${evidence.document_id}-${evidence.page_number}`);
  await reader.getByRole("button", { name: "关闭阅读器" }).click();
  return { document_id: evidence.document_id, page_number: evidence.page_number, painted_canvas: true, screenshot };
}

try {
  const corpus = JSON.parse(await readFile(path.join(repo, "docs/pdf-regression-corpus.json"), "utf8"));
  const libraryResponse = await page.request.get(`${apiUrl}/documents`);
  assert.equal(libraryResponse.status(), 200);
  const library = await libraryResponse.json();
  const docs = ["1706.03762v1.pdf", "1810.04805v2.pdf"].map((filename) => {
    const expected = corpus.downloads.find((item) => item.filename === filename);
    const document = library.items.find((item) => item.original_filename === filename && item.status === "ready");
    assert.ok(document && expected?.sha256, `Missing pinned public document: ${filename}`);
    assert.equal(document.sha256, expected.sha256);
    return document;
  });
  allowedIds = docs.map((item) => item.id);
  report.public_documents = docs.map(({ id, original_filename, sha256 }) => ({ id, original_filename, sha256 }));
  report.synthetic_text_sha256 = createHash("sha256").update(sourceText).digest("hex");
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.getByLabel("工作区导航").getByRole("button", { name: /研究脉络/ }).click();
  await page.getByRole("button", { name: "证据综述", exact: true }).click();
  await page.getByRole("checkbox", { name: "仅整理原文（不调用 LLM）" }).uncheck();
  await page.getByRole("button", { name: "整理综述", exact: true }).waitFor();
  assert.ok(await page.getByRole("button", { name: "整理综述", exact: true }).isDisabled());
  await chooseScope("#research-review", "综述文献范围");
  if (cloudAllowed && ["all", "review"].includes(selectedCase)) {
    const [payload, result] = await submit("/reviews/generate", page.getByRole("button", { name: "整理综述", exact: true }));
    const evidence = checkGrounding(payload);
    assert.equal(payload.review, payload.claims.map((claim) => `${claim.text} [${claim.evidence_ids.join(" ")}]`).join("\n\n"));
    assert.deepEqual(new Set(payload.claims.flatMap((claim) => claim.evidence_ids.map((id) => evidence.get(id).document_id))), new Set(allowedIds));
    await page.locator(".review-narrative").waitFor();
    for (const claim of payload.claims) assert.ok((await page.locator(".review-narrative").innerText()).includes(claim.text));
    result.screenshot = await capture("generated-review");
    result.reader = await checkReader(evidence.get(payload.claims[0].evidence_ids[0]), ".review-claims");
    result.status = "passed";
  }
  await page.getByLabel("工作区导航").getByRole("button", { name: /研究产出/ }).click();
  await page.getByRole("button", { name: "论文框架", exact: true }).click();
  const writing = page.locator("#writing-workbench");
  await writing.getByLabel("研究 idea").fill(idea);
  assert.ok(await writing.getByRole("button", { name: "整理论文框架", exact: true }).isDisabled());
  await chooseScope("#writing-workbench", "写作文献范围");
  await writing.getByLabel("输出语言").selectOption("en");
  if (cloudAllowed && ["all", "writing"].includes(selectedCase)) {
    const [payload, result] = await submit("/writing/outline", writing.getByRole("button", { name: "整理论文框架", exact: true }));
    const evidence = checkGrounding(payload);
    assert.deepEqual(payload.sections.map((item) => item.title), ["Abstract", "Introduction", "Related Work"]);
    assert.deepEqual(new Set(payload.claims.flatMap((claim) => claim.evidence_ids.map((id) => evidence.get(id).document_id))), new Set(allowedIds));
    assert.ok(payload.evidence.every((item) => item.source_type !== "reference"));
    assert.ok(payload.references.length);
    for (const reference of payload.references) {
      const document = docs.find((item) => item.id === reference.document_id);
      assert.ok(document);
      assert.equal(reference.title, document.title);
      assert.ok(reference.authors.length);
      assert.ok(reference.publication_year);
      for (const id of reference.evidence_ids) assert.equal(evidence.get(id).document_id, reference.document_id);
    }
    assert.ok(payload.evidence.every((item) => item.recall_mode === "hybrid" && item.retrieval_mode === "reranked"));
    await writing.locator(".writing-workbench").waitFor();
    for (const reference of payload.references) assert.ok((await writing.locator(".verified-references").innerText()).includes(reference.formatted_citation));
    result.screenshot = await capture("generated-writing");
    result.reader = await checkReader(evidence.get(payload.claims[0].evidence_ids[0]), ".writing-workbench");
    result.status = "passed";
  }
  if (cloudAllowed && ["all", "translation"].includes(selectedCase)) {
    await page.getByRole("button", { name: "学术翻译", exact: true }).click();
    const translation = page.locator("#academic-translation");
    await translation.getByLabel("摘要或论文正文").fill(sourceText);
    await translation.getByLabel("术语表（可选）").fill("检索增强生成 = retrieval-augmented generation");
    const [payload, result] = await submit("/writing/translate", translation.getByRole("button", { name: "翻译并校验", exact: true }));
    for (const text of ["$y=x^2$", "`beta_2 = 0.98`", "[11]", "retrieval-augmented generation", "BLEU"]) assert.ok(payload.translation.includes(text));
    assert.ok(/\b(?:may|might|could)\b/i.test(payload.translation), "Tentative source claim must stay tentative");
    assert.ok(/not|no|yet/i.test(payload.translation), "Absence of performance experiments must not become a reported result");
    assert.ok(!payload.translation.includes("__PAPERPILOT_"));
    const editor = translation.locator(".academic-translation-result textarea");
    await editor.waitFor();
    assert.equal(await editor.inputValue(), payload.translation);
    const [download] = await Promise.all([page.waitForEvent("download"), translation.getByRole("button", { name: "导出 Markdown", exact: true }).click()]);
    const exportedPath = path.join(screenshotDir, "synthetic-translation.md");
    await download.saveAs(exportedPath);
    const exported = await readFile(exportedPath, "utf8");
    assert.ok(exported.includes(payload.translation) && exported.includes(sourceText));
    await translation.locator(".academic-translation-result").scrollIntoViewIfNeeded();
    result.screenshot = await capture("generated-translation");
    result.export = exportedPath;
    result.status = "passed";
  }
  assert.deepEqual(report.page_errors, []);
  assert.deepEqual(report.blocked_requests, []);
  report.status = "passed";
  report.scope_controls = "passed (empty disabled, explicit two public documents)";
} catch (error) {
  report.error = error.message;
  await capture("failure").catch(() => {});
  process.exitCode = 1;
} finally {
  await writeFile(output, JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify({ status: report.status, output, cases: report.cases.map(({ case_id, status, http_status }) => ({ case_id, status, http_status })), request_counts: report.request_counts, error: report.error }));
}
