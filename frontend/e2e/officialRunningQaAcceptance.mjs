// One genuine running-application conversation, scoped to a SHA-verified public PDF.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import { chromium } from "playwright-core";
import { waitForPaintedPdfPage } from "./pdfRenderReady.mjs";

const project = path.resolve("..");
const output = path.resolve(process.env.PDFAGENT_RUNNING_QA_OUTPUT);
assert.ok(output.startsWith(path.join(project, "backend", "tmp") + path.sep));
assert.equal(process.env.PDFAGENT_ALLOW_CLOUD, "true", "Explicit public-excerpt verification required");
const isolatedApi = process.env.PDFAGENT_ACCEPTANCE_API;
const api = new URL(isolatedApi || "http://127.0.0.1:8000");
if (isolatedApi) {
  assert.ok(["localhost", "127.0.0.1"].includes(api.hostname));
  assert.ok(Number(api.port) >= 8011 && Number(api.port) <= 8099);
  assert.equal(api.username + api.password + api.search + api.hash, "");
  assert.ok(api.pathname === "/");
}
const base = new URL(process.env.PDFAGENT_DEMO_URL || "http://localhost:3300");
assert.ok(["localhost", "127.0.0.1"].includes(base.hostname));
const publicSha = "0a9bff1be3575cf9fe26a4a3d3a9175bc791ecec90de8b9deba4cff75d2b9476";
const docs = await (await fetch(new URL("/api/v1/documents?limit=200", api))).json();
const matches = docs.items.filter(document => document.sha256 === publicSha && document.status === "ready");
assert.equal(matches.length, 1);
const selected = matches[0];
const expectedParser = process.env.PDFAGENT_EXPECTED_PARSER_VERSION || "1.26.4-layout98";
assert.equal(selected.parser_version, expectedParser, "Do not record a different parsed version");
const savedTurnFile = process.env.PDFAGENT_QA_RESUME_TURN_FILE ? path.resolve(process.env.PDFAGENT_QA_RESUME_TURN_FILE) : null;
assert.ok(!savedTurnFile || savedTurnFile.startsWith(path.join(project, "backend", "tmp") + path.sep));
const savedTurnBytes = savedTurnFile ? await readFile(savedTurnFile) : null;
const savedTurn = savedTurnBytes ? JSON.parse(savedTurnBytes) : null;
if (savedTurn) assert.deepEqual(savedTurn.conversation.document_ids, [selected.id]);
await mkdir(output);
const report = { status: "running", scope: "actual Docker frontend, Redis worker reparsed public PDF, BGE and DeepSeek, saved conversation, evidence-to-original", source_sha256: publicSha, document_id: selected.id, generation_requests: 0, automatic_retries: 0, checks: [], errors: [] };
if (isolatedApi) report.scope = "current source frontend and isolated real HTTP API, BGE and DeepSeek; not Docker deployment or worker parsing";
if (savedTurn) {
  report.mode = "resume_original_saved_turn_no_generation";
  report.original_turn_sha256 = createHash("sha256").update(savedTurnBytes).digest("hex");
  report.conversation_id = savedTurn.conversation.id;
}
const browser = await chromium.launch({ channel: "msedge", headless: true, args: ["--no-proxy-server"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.setDefaultTimeout(30000);
page.on("pageerror", error => report.errors.push(error.message));
page.on("requestfailed", request => {
  if (new URL(request.url()).pathname.endsWith("/agent/conversations")) {
    report.errors.push(`Conversation list request failed: ${request.failure()?.errorText}`);
  }
});
page.on("response", async response => {
  if (new URL(response.url()).pathname.endsWith("/agent/conversations") && response.request().method() === "GET") {
    report.history_http_status = response.status();
    try {
      const history = await response.json();
      report.history_record_count = history.length;
      report.expected_saved_conversation_listed = savedTurn ? history.some(row => row.id === savedTurn.conversation.id) : null;
    } catch { report.history_body_unavailable = true; }
  }
});
let injectedHistoryFailure = false;
await page.route("**/api/v1/**", async route => {
  const request = route.request(), url = new URL(request.url());
  if (process.env.PDFAGENT_INJECT_HISTORY_FAILURE === "true" && !injectedHistoryFailure && request.method() === "GET" && url.pathname === "/api/v1/agent/conversations") {
    injectedHistoryFailure = true;
    report.injected_first_history_failure = true;
    return route.abort("connectionreset");
  }
  if (request.method() === "GET") return route.continue();
  assert.ok(!savedTurn, "Saved-turn UI continuation must make no POST requests");
  if (request.method() === "POST" && url.pathname === "/api/v1/agent/conversations") {
    assert.deepEqual(request.postDataJSON().document_ids, [selected.id]);
    return route.continue();
  }
  const stream = url.pathname.match(/^\/api\/v1\/agent\/conversations\/([^/]+)\/stream$/);
  if (request.method() === "POST" && stream) {
    assert.equal(report.generation_requests, 0, "No automatic generation retries");
    const conversationResponse = await page.request.get(new URL(`/api/v1/agent/conversations/${stream[1]}`, api).href);
    assert.equal(conversationResponse.status(), 200);
    const conversation = await conversationResponse.json();
    assert.deepEqual(conversation.document_ids, [selected.id]);
    report.conversation_id = conversation.id;
    report.generation_requests += 1;
    await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
    return route.continue();
  }
  report.errors.push(`Prevented unexpected write/generation: ${url.pathname}`);
  return route.abort();
});
try {
  await page.goto(base.href, { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作区导航").getByRole("button", { name: /文献问答/ }).click();
  await page.getByRole("button", { name: "连续问答", exact: true }).click();
  if (savedTurn) {
    const retry = page.getByRole("button", { name: "重新读取历史", exact: true });
    const target = page.locator(".chat-history-item").filter({ hasText: savedTurn.conversation.title });
    await Promise.race([retry.waitFor({ state: "visible" }), target.waitFor({ state: "visible" })]);
    if (await retry.isVisible()) {
      report.recovered_history_load_failure = true;
      await retry.click();
      await target.waitFor({ state: "visible" });
    }
  }
  let turn = savedTurn;
  if (savedTurn) {
    await page.locator(".chat-history-item").filter({ hasText: savedTurn.conversation.title }).getByRole("button").first().click();
    await page.locator(".chat-message.assistant .chat-claims").waitFor();
  } else {
  await page.getByLabel("新会话检索范围").selectOption(selected.id);
  const question = "这篇论文中提到的 Transformer 架构，在训练 Base 模型时使用了多少个 GPU？具体是什么型号？";
  await page.locator(".chat-composer textarea").fill(question);
  const responsePromise = page.waitForResponse(response => /\/agent\/conversations\/[^/]+\/stream$/.test(new URL(response.url()).pathname), { timeout: 180000 });
  await page.getByRole("button", { name: "检索并回答", exact: true }).click();
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  const raw = await response.text();
  const done = raw.split(/\r?\n\r?\n/).find(block => block.startsWith("event: done\n"));
  assert.ok(done, "Streaming response must finish; HTTP 200 alone is not acceptance");
  turn = JSON.parse(done.split("\ndata: ")[1]);
  await writeFile(path.join(output, "turn.json"), JSON.stringify(turn, null, 2));
  }
  const message = turn.assistant_message;
  assert.equal(message.provider, "deepseek");
  assert.equal(message.model, "deepseek-flash");
  assert.equal(message.insufficient_evidence, false);
  const anchors = new Map(message.evidence.map(item => [item.evidence_id, item]));
  assert.ok(message.claims.some(claim => /8|八/.test(claim.text) && claim.text.includes("P100")));
  for (const claim of message.claims) for (const key of claim.evidence_ids) {
    const anchor = anchors.get(key);
    assert.equal(anchor.document_id, selected.id);
    assert.equal(anchor.recall_mode, "hybrid");
    assert.equal(anchor.retrieval_mode, "reranked");
  }
  await page.getByRole("button", { name: "检索并回答", exact: true }).waitFor();
  await page.locator(".chat-message.assistant .chat-claims button").first().waitFor();
  report.checks.push({ name: savedTurn ? "original scoped BGE/DeepSeek turn restored without new generation" : "scoped one-attempt streamed BGE/DeepSeek answer persisted", passed: true, claims: message.claims.length });
  // Crop out history and other document names; no private data in demo screenshots.
  await page.locator(".chat-main").screenshot({ path: path.join(output, "01-public-answer.png") });
  const hardware = message.evidence.find(item => item.page_number === 7 && item.quote.includes("P100") && item.quote.includes("5.2"));
  assert.ok(hardware);
  await page.locator(".chat-message.assistant .chat-claims").getByRole("button", { name: `${hardware.evidence_id} · p.7`, exact: true }).first().click();
  const reader = page.getByRole("dialog", { name: /阅读/ });
  await waitForPaintedPdfPage(reader, 7);
  assert.equal(await reader.locator("#pdf-page-number").inputValue(), "7");
  await reader.locator(".pdf-evidence-highlight").waitFor({ state: "visible" });
  await reader.screenshot({ path: path.join(output, "02-hardware-original-page.png") });
  report.checks.push({ name: "claim opens physical page 7 with source overlay", passed: true });
  await reader.getByRole("button", { name: "关闭阅读器" }).click();
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByLabel("工作区导航").getByRole("button", { name: /文献问答/ }).click();
  await page.getByRole("button", { name: "连续问答", exact: true }).click();
  await page.locator(".chat-history-item").filter({ hasText: turn.conversation.title }).getByRole("button").first().click();
  await page.locator(".chat-message.assistant .chat-claims").waitFor();
  assert.match(await page.locator(".chat-message.assistant .chat-bubble > p").innerText(), /P100/);
  report.checks.push({ name: "reload restores saved public question, claims and citations", passed: true });
  assert.ok(report.errors.every(error => error.startsWith("Conversation list request failed:") && report.recovered_history_load_failure));
  report.status = "machine_passed_pending_source_review";
} catch (error) {
  report.status = "failed";
  report.errors.push(error.message);
  await page.locator(".chat-main").screenshot({ path: path.join(output, "failure-public-chat.png") }).catch(() => {});
  process.exitCode = 1;
} finally {
  await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify(report, null, 2));
}
