import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { chromium } from "playwright-core";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const argumentsByName = Object.fromEntries(
  process.argv.slice(2).map((value, index, all) =>
    value.startsWith("--") ? [value.slice(2), all[index + 1]] : null,
  ).filter(Boolean),
);
const baseUrl = (argumentsByName.url || "http://localhost:3000").replace(/\/$/, "");
const backendReportPath = argumentsByName["backend-report"];
const outputPath = argumentsByName.output;
if (!backendReportPath || !outputPath) {
  throw new Error("Usage: node realPdfComparison.mjs --url URL --backend-report FILE --output FILE");
}

const dataset = JSON.parse(await readFile(path.join(repoRoot, "backend/evals/real_pdf_comparison_grounded.json"), "utf8"));
const backendReport = JSON.parse(await readFile(backendReportPath, "utf8"));
assert.equal(backendReport.status, "passed", "The pinned real-PDF backend gate must pass first");
assert.equal(backendReport.dataset_id, dataset.dataset_id, "Browser and backend gates must use the same dataset");
const selectedCases = argumentsByName["case-id"]
  ? dataset.cases.filter((item) => item.case_id === argumentsByName["case-id"])
  : dataset.cases;
assert.ok(selectedCases.length > 0, "Requested case ID is not present in the dataset");

const browser = await chromium.launch({
  channel: process.env.PDFAGENT_BROWSER_CHANNEL || (process.platform === "win32" ? "msedge" : "chrome"),
  headless: true,
});
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(30_000);
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));
const report = {
  dataset_id: dataset.dataset_id,
  frontend_url: baseUrl,
  backend_report: path.resolve(backendReportPath),
  status: "failed",
  upload: null,
  cases: [],
  page_errors: pageErrors,
};
const screenshotDir = path.join(path.dirname(outputPath), "screenshots");

function containsAny(value, options) {
  const normalize = (text) => text.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  const haystack = normalize(value);
  return options.some((option) => haystack.includes(normalize(option)));
}

async function selectDocuments(documentIds) {
  const list = page.locator(".comparison-document-list");
  for (const documentId of documentIds) {
    await list.locator(`[data-document-id="${documentId}"]`).waitFor({ state: "visible" });
  }
  for (const label of await list.locator("label[data-document-id]").all()) {
    const documentId = await label.getAttribute("data-document-id");
    const checkbox = label.locator("input[type=checkbox]");
    if (!documentIds.includes(documentId) && await checkbox.isChecked()) {
      await checkbox.uncheck();
    }
  }
  for (const documentId of documentIds) {
    const checkbox = list.locator(`[data-document-id="${documentId}"] input[type=checkbox]`);
    if (!await checkbox.isChecked()) await checkbox.check();
  }
  const selected = await list.locator("label.selected").evaluateAll((labels) =>
    labels.map((label) => label.getAttribute("data-document-id")),
  );
  assert.deepEqual(new Set(selected), new Set(documentIds));
}

async function testDuplicateUpload() {
  const uploadPath = path.join(repoRoot, "output/pdf/regression-corpus/1810.04805v2.pdf");
  const [response] = await Promise.all([
    page.waitForResponse((candidate) => {
      const url = new URL(candidate.url());
      return candidate.request().method() === "POST" && url.pathname.endsWith("/api/v1/documents");
    }),
    page.locator('input[accept="application/pdf,.pdf"]').setInputFiles(uploadPath),
  ]);
  assert.equal(response.status(), 202, "Duplicate PDF upload must succeed");
  const body = await response.json();
  assert.equal(body.exact_duplicate, true, "The original paper must already be indexed");
  await page.locator(".notice.success").getByText("完全重复内容").waitFor();
  return { filename: "1810.04805v2.pdf", exact_duplicate: true, document_id: body.document.id };
}

async function verifyReader(evidence, caseId) {
  const source = page.locator(`.comparison-claims button[data-evidence-id="${evidence.evidence_id}"]`).first();
  await source.click();
  const dialog = page.getByRole("dialog", { name: /阅读/ });
  await dialog.waitFor({ state: "visible" });
  assert.equal(await dialog.locator("#pdf-page-number").inputValue(), String(evidence.page_number));
  await dialog.locator(".pdf-evidence-status", { hasText: "已高亮原文证据" }).waitFor({ timeout: 45_000 });
  assert.equal(await dialog.locator(".pdf-reader-state.error").count(), 0);
  await page.screenshot({ path: path.join(screenshotDir, `${caseId}-source.png`), fullPage: false });
  await dialog.getByRole("button", { name: "关闭阅读器" }).click();
  await dialog.waitFor({ state: "hidden" });
  return { evidence_id: evidence.evidence_id, document_id: evidence.document_id, page: evidence.page_number };
}

async function runCase(testCase, backendCase) {
  const documentIds = testCase.documents.map((filename) => backendCase.document_ids[filename]);
  assert.ok(documentIds.every(Boolean), `Missing pinned document ID for ${testCase.case_id}`);
  await selectDocuments(documentIds);
  await page.locator("#comparison-question").fill(testCase.question);
  const [response] = await Promise.all([
    page.waitForResponse((candidate) => {
      const url = new URL(candidate.url());
      return candidate.request().method() === "POST" && url.pathname.endsWith("/api/v1/agent/compare");
    }, { timeout: 150_000 }),
    page.getByRole("button", { name: "开始证据对比" }).click(),
  ]);
  assert.equal(response.status(), 200, `${testCase.case_id}: comparison API failed`);
  assert.ok(
    response.request().postDataJSON().evidence_per_document >= testCase.evidence_per_document,
    `${testCase.case_id}: browser requested fewer passages than the backend gold gate`,
  );
  const body = await response.json();
  report.current_case = {
    case_id: testCase.case_id,
    answer: body.answer,
    claims: body.claims,
    audit: body.audit,
    warnings: body.warnings,
    trace: body.trace,
    evidence: body.evidence.map((item) => ({
      evidence_id: item.evidence_id,
      document_id: item.document_id,
      page_number: item.page_number,
      quote: item.quote,
    })),
  };
  await page.locator(".comparison-result").waitFor({ state: "visible" });
  assert.equal(await page.locator(".comparison-error").count(), 0);
  assert.equal(body.provider, "deepseek");
  assert.equal(body.model, "deepseek-flash");
  assert.ok(body.evidence.length > 0);
  assert.ok(body.evidence.every((item) => item.retrieval_mode === "reranked"));
  assert.equal(
    (await page.locator(".comparison-answer").innerText()).trim(),
    body.answer.trim(),
    "The visible answer must match the actual API response",
  );
  assert.equal(await page.locator(".comparison-claims article").count(), body.claims.length);
  const evidenceById = new Map(body.evidence.map((item) => [item.evidence_id, item]));
  for (const [index, claim] of body.claims.entries()) {
    assert.ok(claim.evidence_ids.length > 0, `${testCase.case_id}: claim lacks evidence`);
    assert.ok(claim.evidence_ids.every((id) => evidenceById.has(id)));
    const card = page.locator(".comparison-claims article").nth(index);
    assert.equal((await card.locator("p").innerText()).trim(), claim.text.trim());
    for (const evidenceId of claim.evidence_ids) {
      assert.equal(await card.locator(`button[data-evidence-id="${evidenceId}"]`).count(), 1);
    }
  }
  for (const forbidden of testCase.forbidden_claim_text_contains_any || []) {
    assert.ok(
      body.claims.every((claim) => !containsAny(claim.text, [forbidden])),
      `${testCase.case_id}: published related-work content ${forbidden}`,
    );
  }
  const factChecks = Object.fromEntries((testCase.required_claim_facts || []).map((fact) => {
    const sourceId = backendCase.document_ids[fact.source_filename];
    const passed = body.claims.some((claim) =>
      containsAny(claim.text, fact.claim_contains_any)
      && (fact.claim_contains_all || []).every((fragment) => containsAny(claim.text, [fragment]))
      && claim.evidence_ids.some((id) => {
        const anchor = evidenceById.get(id);
        return anchor?.document_id === sourceId
          && fact.source_pages.includes(anchor.page_number)
          && containsAny(anchor.quote, fact.source_text_contains_any);
      }),
    );
    return [fact.fact_id, passed];
  }));
  assert.ok(Object.values(factChecks).every(Boolean),
    `${testCase.case_id}: missing source-anchored facts ${JSON.stringify(factChecks)}`);
  const result = {
    case_id: testCase.case_id,
    document_ids: documentIds,
    claim_count: body.claims.length,
    evidence_count: body.evidence.length,
    retrieval_modes: [...new Set(body.evidence.map((item) => item.retrieval_mode))],
    insufficient_evidence: body.insufficient_evidence,
    facts: factChecks,
    reader: null,
  };
  if (testCase.must_refuse) {
    assert.equal(body.insufficient_evidence, true);
    assert.equal(body.audit.cross_document_claim_count, 0);
    if (testCase.forbidden_quantity_pattern) {
      const inventedQuantity = new RegExp(testCase.forbidden_quantity_pattern, "i");
      assert.ok(body.claims.every((claim) => !inventedQuantity.test(claim.text)),
        `${testCase.case_id}: unsupported target quantity was published`);
    }
    await page.locator(".comparison-result-heading", { hasText: "证据不足" }).waitFor();
  } else {
    assert.equal(body.insufficient_evidence, false);
    assert.ok(body.claims.length > 0);
    assert.ok(body.audit.cross_document_claim_count >= testCase.min_cross_document_claims);
    assert.equal(body.audit.referenced_document_count, documentIds.length);
    const firstCitedEvidence = body.claims.flatMap((claim) => claim.evidence_ids)
      .map((id) => evidenceById.get(id)).find(Boolean);
    assert.ok(firstCitedEvidence, "A published claim must link to original PDF evidence");
    result.reader = await verifyReader(firstCitedEvidence, testCase.case_id);
  }
  await page.screenshot({ path: path.join(screenshotDir, `${testCase.case_id}.png`), fullPage: false });
  delete report.current_case;
  return result;
}

try {
  await mkdir(screenshotDir, { recursive: true });
  await page.goto(`${baseUrl}/#paper-comparison`, { waitUntil: "domcontentloaded" });
  await page.locator("section.paper-comparison-section").waitFor({ state: "visible" });
  const firstPinnedId = backendReport.cases[0].document_ids[dataset.cases[0].documents[0]];
  await page.locator(`[data-document-id="${firstPinnedId}"]`).waitFor({ state: "visible" });
  report.upload = await testDuplicateUpload();
  for (const testCase of selectedCases) {
    const backendCase = backendReport.cases.find((item) => item.case_id === testCase.case_id);
    assert.ok(backendCase?.passed, `Backend gold gate failed for ${testCase.case_id}`);
    report.cases.push(await runCase(testCase, backendCase));
  }
  assert.equal(pageErrors.length, 0, `Browser errors: ${pageErrors.join("; ")}`);
  report.status = "passed";
} catch (error) {
  report.error = error.stack || String(error);
  await page.screenshot({ path: path.join(screenshotDir, "failure.png"), fullPage: false }).catch(() => {});
} finally {
  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, JSON.stringify(report, null, 2) + "\n", "utf8");
  await browser.close();
}

console.log(JSON.stringify({ status: report.status, upload: report.upload, cases: report.cases, error: report.error }, null, 2));
if (report.status !== "passed") process.exitCode = 1;
