/** Current UI + real public arXiv/BGE API in the dedicated 3239/8039 fixture. */
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const output = path.resolve(process.argv[2] || "");
const relative = path.relative(path.join(repo, "backend/tmp"), output);
assert.ok(relative && !relative.startsWith("..") && !path.isAbsolute(relative));
await mkdir(output, { recursive: false });
const api = "http://127.0.0.1:8039/api/v1";
const browser = await chromium.launch({ channel: "msedge", headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(60_000);
const report = { status: "running", checks: {}, page_errors: [], forbidden_requests: [],
  scope: "Real public recommendation UI/API, eager/in-memory task mode; external Worker/Beat checked separately",
  injected_errors: 0, cloud_calls: 0, production_library_modified: false };
page.on("pageerror", (error) => report.page_errors.push(error.message));
page.on("dialog", (dialog) => dialog.accept());
await context.route("**/api/v1/**", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  if (url.origin !== "http://127.0.0.1:8039"
      || (!["GET", "HEAD", "OPTIONS"].includes(request.method())
      && !url.pathname.startsWith("/api/v1/recommendations"))) {
    report.forbidden_requests.push(url.pathname);
    return route.abort();
  }
  return route.continue();
});
const feedbackRequest = (id) => (response) => response.request().method() === "POST"
  && new URL(response.url()).pathname === `/api/v1/recommendations/${id}/feedback`;

try {
  const subscriptions = (await (await context.request.get(`${api}/recommendations/subscriptions`)).json()).items;
  const active = subscriptions.find((item) => item.name === "Acceptance active_b");
  assert.ok(active);
  const original = await (await context.request.get(`${api}/recommendations?subscription_id=${active.id}`)).json();
  assert.equal(original.total, 2);
  const sample = original.items[0];
  await page.goto("http://127.0.0.1:3239/#recommendations", { waitUntil: "domcontentloaded" });
  const card = page.locator(".recommendation-card").filter({ has: page.getByRole("heading", { name: sample.title, exact: true }) });
  await card.waitFor();
  assert.ok(await card.locator(".recommendation-reason").innerText());
  report.checks.live_saved_papers_and_reasons_visible = true;
  const like = card.getByTitle("喜欢，影响后续推荐", { exact: true });
  const dislike = card.getByTitle("不感兴趣，影响后续推荐", { exact: true });
  if (sample.feedback !== "neutral") {
    const reset = sample.feedback === "like" ? like : dislike;
    const [resetResponse] = await Promise.all([page.waitForResponse(feedbackRequest(sample.id)), reset.click()]);
    assert.equal((await resetResponse.json()).feedback, "neutral");
    report.precondition_feedback_reset = sample.feedback;
  }
  let [response] = await Promise.all([page.waitForResponse(feedbackRequest(sample.id)), like.click()]);
  assert.equal(response.status(), 200);
  assert.equal((await response.json()).feedback, "like");
  await page.reload({ waitUntil: "domcontentloaded" });
  await card.waitFor();
  assert.equal(await like.getAttribute("aria-pressed"), "true");
  report.checks.like_persists_after_reload = true;
  [response] = await Promise.all([page.waitForResponse(feedbackRequest(sample.id)), like.click()]);
  assert.equal((await response.json()).feedback, "neutral");
  [response] = await Promise.all([page.waitForResponse(feedbackRequest(sample.id)), dislike.click()]);
  assert.equal((await response.json()).feedback, "dislike");
  report.checks.toggle_like_neutral_dislike = true;
  const chip = page.locator(".feed-chip").filter({ hasText: active.name });
  [response] = await Promise.all([
    page.waitForResponse((r) => new URL(r.url()).pathname.endsWith(`/subscriptions/${active.id}/refresh`)),
    chip.getByRole("button", { name: "刷新", exact: true }).click(),
  ]);
  assert.equal(response.status(), 202);
  const after = await (await context.request.get(`${api}/recommendations?subscription_id=${active.id}`)).json();
  assert.deepEqual(new Set(after.items.map((item) => item.id)), new Set(original.items.map((item) => item.id)));
  assert.equal(after.items.find((item) => item.id === sample.id).feedback, "dislike");
  assert.ok(after.items.every((item) => item.ranking_provider === "openai-compatible" && !item.ranking_warning));
  await writeFile(path.join(output, "after-live-refresh.json"), JSON.stringify(after, null, 2));
  report.checks.manual_live_refresh_keeps_feedback_without_hash_fallback = true;
  await page.getByLabel("追踪名称", { exact: true }).fill("Browser acceptance temporary");
  await page.getByLabel("研究关键词", { exact: true }).fill("low-rank adaptation");
  [response] = await Promise.all([
    page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/v1/recommendations/subscriptions"),
    page.getByRole("button", { name: "创建追踪", exact: true }).click(),
  ]);
  assert.equal(response.status(), 201);
  const created = await response.json();
  assert.equal(created.last_error, null);
  const temporary = page.locator(".feed-chip").filter({ hasText: created.name });
  await temporary.waitFor();
  const newly = await (await context.request.get(`${api}/recommendations?subscription_id=${created.id}`)).json();
  assert.ok(newly.total > 0 && newly.items.every((item) => item.ranking_provider === "openai-compatible" && !item.ranking_warning));
  report.created_public_papers = newly.total;
  report.checks.create_subscription_runs_first_real_refresh = true;
  [response] = await Promise.all([
    page.waitForResponse((r) => r.request().method() === "DELETE" && new URL(r.url()).pathname.endsWith(`/subscriptions/${created.id}`)),
    temporary.getByRole("button", { name: "×", exact: true }).click(),
  ]);
  assert.equal(response.status(), 204);
  await temporary.waitFor({ state: "hidden" });
  assert.equal((await (await context.request.get(`${api}/recommendations?subscription_id=${created.id}`)).json()).total, 0);
  assert.equal((await (await context.request.get(`${api}/recommendations?subscription_id=${active.id}`)).json()).total, 2);
  report.checks.delete_via_ui_cascades_only_its_own_papers = true;
  await page.route(`**/recommendations/${sample.id}/feedback`, async (route) => {
    report.injected_errors++;
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "验收注入：反馈暂不可用" }) });
  }, { times: 1 });
  await like.click();
  await page.getByText(/验收注入：反馈暂不可用/).waitFor();
  assert.equal(await dislike.getAttribute("aria-pressed"), "true");
  [response] = await Promise.all([page.waitForResponse(feedbackRequest(sample.id)), like.click()]);
  assert.equal((await response.json()).feedback, "like");
  await page.getByText(/验收注入：反馈暂不可用/).waitFor({ state: "hidden" });
  report.checks.feedback_failure_visible_without_false_state_change_manual_recovery = true;
  await page.screenshot({ path: path.join(output, "desktop.png"), fullPage: false });
  await page.setViewportSize({ width: 390, height: 844 });
  await card.scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 2), true);
  await page.screenshot({ path: path.join(output, "mobile.png"), fullPage: false });
  report.checks.mobile_no_horizontal_page_overflow = true;
  assert.deepEqual(report.page_errors, []);
  assert.deepEqual(report.forbidden_requests, []);
  report.checks.no_page_errors_generation_or_import_requests = true;
  report.status = "passed_scoped_checks";
} catch (error) {
  report.status = "failed";
  report.error = { type: error.name, message: error.message, stack: error.stack };
  await page.screenshot({ path: path.join(output, "failure.png") }).catch(() => {});
} finally {
  await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ status: report.status, checks: report.checks, error: report.error?.message }));
process.exitCode = report.status === "passed_scoped_checks" ? 0 : 1;
