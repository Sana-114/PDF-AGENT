// Latest-source UI + real isolated CSV API. No mocked response bodies.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright-core";

const project = path.resolve("..");
const root = path.resolve(process.env.PDFAGENT_CHART_INPUTS);
const output = path.resolve(process.env.PDFAGENT_CHART_OUTPUT);
const api = new URL(process.env.PDFAGENT_ACCEPTANCE_API || "http://127.0.0.1:8013");
const base = new URL(process.env.PDFAGENT_DEMO_URL || "http://127.0.0.1:3301");
assert.ok([base, api].every(url => ["127.0.0.1", "localhost"].includes(url.hostname)));
assert.ok(Number(api.port) >= 8011 && Number(api.port) <= 8099, "Only isolated acceptance API ports are allowed");
assert.ok([root, output].every(p => p.startsWith(path.join(project, "backend", "tmp") + path.sep)));
const inputs = JSON.parse(await readFile(path.join(root, "report.json"), "utf8"));
assert.equal(inputs.status, "passed");
await mkdir(output); // Never overwrite failures.
const browser = await chromium.launch({ channel: "msedge", headless: true, args: ["--no-proxy-server"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, acceptDownloads: true });
const report = { status: "running", scope: "real frontend and isolated actual backend", frontend_url: base.href, isolated_api_url: api.href, cases: [], errors: [], cloud_calls: 0 };
page.on("pageerror", error => report.errors.push(error.message));
await page.route("**/api/v1/**", async route => {
  const request = route.request();
  const url = new URL(request.url());
  assert.ok(request.method() === "GET" || (request.method() === "POST" && url.pathname === "/api/v1/visualizations/analyze"));
  await route.continue({ url: new URL(url.pathname + url.search, api).href });
});

try {
  await page.goto(new URL("/#data-visualization", base).href, { waitUntil: "domcontentloaded" });
  const workspace = page.locator(".data-visualization-workbench");
  await workspace.waitFor({ state: "visible" });
  for (const name of ["table3-line", "table3-bar", "table3-radar", "synthetic-missing-duplicate", "synthetic-unsorted-dates", "synthetic-replicates"]) {
    const accepted = JSON.parse(await readFile(path.join(root, name + ".json"), "utf8"));
    const chart = accepted.chart;
    await workspace.locator('input[type="file"]').setInputFiles(path.join(root, name + ".csv"));
    await workspace.getByLabel("图表类型").selectOption(chart.chart_type);
    const responsePromise = page.waitForResponse(r => r.url().includes("/visualizations/analyze") && r.request().method() === "POST");
    await workspace.getByRole("button", { name: "绘制图表", exact: true }).click();
    let response = await responsePromise;
    assert.equal(response.status(), 200);
    await workspace.locator(".scientific-chart-svg").waitFor();
    if (chart.chart_type !== "radar3d") {
      await workspace.getByLabel("X 轴 / 类别").selectOption(chart.x_column);
      const config = workspace.getByRole("region", { name: "科研图表配置" });
      for (const column of accepted.columns.filter(c => c.kind === "numeric" && c.name !== chart.x_column)) {
        const input = config.getByLabel(column.name, { exact: true });
        await input.setChecked(chart.y_columns.includes(column.name));
      }
      if (chart.group_column) await workspace.getByLabel("分组列").selectOption(chart.group_column);
      await workspace.getByLabel("聚合方式").selectOption(chart.aggregation);
      await workspace.getByLabel("误差棒").selectOption(chart.error_mode);
      const configuredResponse = page.waitForResponse(r => r.url().includes("/visualizations/analyze") && r.request().method() === "POST");
      await workspace.getByRole("button", { name: "应用配置", exact: true }).click();
      response = await configuredResponse;
    }
    const payload = await response.json();
    assert.deepEqual(payload.chart.series, chart.series);
    await page.waitForFunction(expected => document.querySelector(".figure-caption")?.textContent === expected, chart.caption);
    const svg = workspace.locator(".scientific-chart-svg");
    assert.equal(await svg.locator("title").first().textContent(), chart.title);
    const checks = { real_api_same_values: true, preview_rows: await workspace.locator(".csv-data-panel table").first().locator("tbody tr").count() === accepted.preview_rows.length };
    if (name === "table3-line") {
      const xs = await svg.locator("circle").evaluateAll(nodes => nodes.map(node => Number(node.getAttribute("cx"))));
      checks.true_numeric_x_spacing = xs.length === 3 && Math.abs((xs[1] - xs[0]) / (xs[2] - xs[0]) - 1 / 3) < 1e-9;
    }
    if (name === "synthetic-missing-duplicate") {
      checks.complete_polygons = await svg.locator("polygon.radar-series").count() === 2;
      checks.gap_outlines = await svg.locator("polyline.radar-series").count() === 1;
      checks.distinct_records = await workspace.locator(".scientific-chart-legend span").count() === 3;
    }
    const downloadPromise = page.waitForEvent("download");
    await workspace.getByRole("button", { name: "下载 SVG", exact: true }).click();
    const download = await downloadPromise;
    const saved = path.join(output, download.suggestedFilename());
    await download.saveAs(saved);
    const exported = await readFile(saved, "utf8");
    checks.export_has_no_invalid_coordinates = !/NaN|Infinity|undefined/.test(exported);
    checks.export_svg_title = exported.includes(chart.title);
    await workspace.locator(".scientific-figure-card").screenshot({ path: path.join(output, name + ".png") });
    report.cases.push({ name, checks, passed: Object.values(checks).every(Boolean) });
    await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));
    assert.ok(Object.values(checks).every(Boolean));
  }
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
