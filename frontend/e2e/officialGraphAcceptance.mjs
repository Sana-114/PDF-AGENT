// Real Docker frontend/API; audited 20-paper fixture, no mocked response bodies.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright-core";
import { waitForPaintedPdfPage } from "./pdfRenderReady.mjs";

const project = path.resolve("..");
const fixture = path.resolve(process.env.PDFAGENT_ACCEPTANCE_FIXTURE);
const output = path.resolve(process.env.PDFAGENT_GRAPH_OUTPUT);
const expectedFile = path.resolve(process.env.PDFAGENT_GRAPH_EXPECTED);
assert.ok([fixture, output, expectedFile].every(p => p.startsWith(path.join(project, "backend", "tmp") + path.sep)));
const api = new URL(process.env.PDFAGENT_ACCEPTANCE_API);
const base = new URL(process.env.PDFAGENT_DEMO_URL);
assert.ok([api, base].every(url => ["localhost", "127.0.0.1"].includes(url.hostname)));
assert.ok(Number(api.port) >= 8011 && Number(api.port) <= 8099);
const rows = JSON.parse(await readFile(path.join(fixture, "fixture.json"), "utf8"));
const expected = JSON.parse(await readFile(expectedFile, "utf8"));
assert.equal(rows.length, 20);
await mkdir(output);
const browser = await chromium.launch({ channel: "msedge", headless: true, args: ["--no-proxy-server"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const report = { status: "running", scope: "real Docker frontend/API; original 20-paper graph", cloud_calls: 0, checks: [], errors: [] };
page.on("pageerror", error => report.errors.push(error.message));
await page.route("**/api/v1/**", async route => {
  const request = route.request(), url = new URL(request.url());
  assert.ok(request.method() === "GET" || (request.method() === "POST" && url.pathname === "/api/v1/citation-graph"));
  await route.continue({ url: new URL(url.pathname + url.search, api).href });
});
try {
  await page.goto(new URL("/#citation-graph", base).href, { waitUntil: "domcontentloaded" });
  const section = page.locator("#citation-graph");
  await section.locator(".document-scope-picker > summary").click();
  const scope = section.getByRole("group", { name: "图谱文献范围" });
  assert.equal(await scope.locator("input:checked").count(), 0);
  assert.ok(await section.getByRole("button", { name: "计算图谱", exact: true }).isDisabled());
  for (const row of rows) await scope.locator(`label[data-document-id="${row.document_id}"] input`).check();
  await section.locator(".document-scope-picker > summary").click();
  const responsePromise = page.waitForResponse(response => response.url().includes("/citation-graph") && response.request().method() === "POST");
  await section.getByRole("button", { name: "计算图谱", exact: true }).click();
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  assert.deepEqual([...JSON.parse(response.request().postData()).document_ids].sort(), rows.map(row => row.document_id).sort());
  const graph = await response.json();
  await writeFile(path.join(output, "graph.json"), JSON.stringify(graph, null, 2));
  assert.deepEqual(graph.stats, expected.stats);
  assert.deepEqual([...graph.edges].sort((a, b) => a.edge_id.localeCompare(b.edge_id)), [...expected.edges].sort((a, b) => a.edge_id.localeCompare(b.edge_id)));
  const canvas = section.getByRole("img", { name: "局域引用关系图" });
  await canvas.waitFor();
  assert.equal(await canvas.locator(".citation-nodes > g").count(), 20);
  assert.ok(expected.edges.length > 0);
  assert.equal(await canvas.locator(".citation-edges > line").count(), expected.edges.length);
  report.checks.push({ name: `20 nodes / ${expected.edges.length} source-audited relations rendered`, passed: true });
  const transformer = graph.nodes.find(node => node.arxiv_id === "1706.03762");
  await canvas.getByRole("button", { name: /Attention Is All You Need/ }).click();
  assert.equal(await section.locator(".citation-inspector h3").textContent(), transformer.title);
  await section.screenshot({ path: path.join(output, "01-graph-20.png") });
  report.checks.push({ name: "node selection and local role metrics", passed: true, role: transformer.role, in_degree: transformer.in_degree });
  await section.getByRole("button", { name: "打开原文", exact: true }).click();
  const reader = page.getByRole("dialog", { name: /阅读/ });
  await waitForPaintedPdfPage(reader, 1);
  await page.screenshot({ path: path.join(output, "02-node-original-page.png") });
  report.checks.push({ name: "graph node opens actual PDF page", passed: true, filename: "1706.03762v1.pdf" });
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
  console.log(JSON.stringify(report, null, 2));
}
