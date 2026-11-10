// Real cached public translations and PDF canvas; never substitute responses.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright-core';
import { waitForPaintedPdfPage } from './pdfRenderReady.mjs';

const output = path.resolve(process.env.PDFAGENT_READING_OUTPUT || '../backend/tmp/demo-final-20261110/reading-take1');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 960 },
  recordVideo: { dir: path.join(output, 'raw'), size: { width: 1920, height: 960 } }, acceptDownloads: true });
const page = await context.newPage();
page.setDefaultTimeout(45000);
const report = { status: 'running', mocked_responses: false, checks: [], errors: [] };
page.on('pageerror', error => report.errors.push(error.message));
let translated;
page.on('response', async response => {
  if (/\/translations\/pages\/\d+$/.test(new URL(response.url()).pathname) && response.ok()) translated = await response.json();
});
const recording = process.env.PDFAGENT_READING_RECORDING === 'true';
const hold = ms => page.waitForTimeout(ms * (recording && ms >= 3000 ? 2.8 : 1));
const start = Date.now();
try {
  await page.goto('http://127.0.0.1:3344/', { waitUntil: 'networkidle' });
  await page.getByLabel('工作区导航').getByRole('button', { name: /文献库/ }).click();
  const paper = process.env.PDFAGENT_READING_PAPER || 'arXiv:1706.03762v1';
  await page.locator('.document-card').filter({ hasText: paper }).first().getByRole('button', { name: '在线阅读', exact: true }).click();
  const reader = page.getByRole('dialog', { name: /阅读/ });
  await waitForPaintedPdfPage(reader, 1);
  report.ui_ready_seconds = (Date.now() - start) / 1000;
  await reader.getByRole('button', { name: '中英', exact: true }).click();
  await reader.locator('.pdf-bilingual-segments article').first().waitFor();
  await hold(2500);
  assert.ok(await reader.locator('.pdf-reader-body.outline-collapsed').count());
  const segments = translated.segments.filter(s => s.bbox && s.source_text.length > 200);
  const abstract = segments.find(s => /dominant|transduction|recurrent|convolutional/i.test(s.source_text)) || segments[0];
  assert.ok(abstract);
  const article = reader.locator(`[data-block-id="${abstract.block_id}"]`);
  await article.locator('button').click();
  await hold(700);
  await reader.locator('.pdf-translation-highlight').waitFor();
  assert.ok(await article.evaluate(el => el.classList.contains('active')));
  const rects = await reader.evaluate(el => {
    const a = el.querySelector('.pdf-translation-highlight').getBoundingClientRect();
    const b = el.querySelector('.pdf-reader-viewport').getBoundingClientRect();
    return { visible: a.bottom > b.top && a.top < b.bottom, fits: el.querySelector('.pdf-page-shell').getBoundingClientRect().width <= b.width,
      shell: el.querySelector('.pdf-page-shell').getBoundingClientRect().width, viewport: b.width };
  });
  report.geometry = rects;
  assert.ok(rects.visible && rects.fits);
  assert.equal(await article.locator('p').evaluate(el => getComputedStyle(el).fontSize), '16px');
  await page.screenshot({ path: path.join(output, 'abstract.png') });
  await hold(5000);
  await article.locator('summary').click(); await hold(3000);
  assert.equal((await article.locator('blockquote').innerText()).trim(), abstract.source_text.trim());
  report.checks.push('abstract translation -> exact source block, painted PDF highlight, readable 16px text');
  await article.locator('summary').click();
  // Click original coordinates; verify the matching translated block, not a scroll ratio.
  const other = segments.filter(s => s.block_id !== abstract.block_id)
    .sort((a, b) => b.bbox[0] - a.bbox[0])[0] || abstract;
  await reader.locator(`[data-block-id="${other.block_id}"] button`).click(); await hold(500);
  const pdf = reader.locator('.pdf-page-shell');
  const size = await pdf.boundingBox();
  // Click the currently highlighted real block; coordinates come from the drawn overlay.
  const highlight = await reader.locator('.pdf-translation-highlight').boundingBox();
  assert.ok(size && highlight);
  await page.mouse.click(highlight.x + highlight.width / 2, highlight.y + Math.min(12, highlight.height / 2));
  await hold(1000);
  assert.ok(await reader.locator(`[data-block-id="${other.block_id}"].active`).count());
  report.checks.push('original click -> corresponding translated block');
  await reader.getByLabel('译文字号').selectOption('18'); await hold(600);
  await reader.locator('.pdf-bilingual-pane').evaluate(el => { el.scrollTop += 220; }); await hold(1200);
  assert.ok(await reader.locator('.pdf-translation-highlight').count());
  report.checks.push('translation scrolling retains a source anchor; font change supported');
  await page.screenshot({ path: path.join(output, 'reading.png') }); await hold(4000);
  for (const width of recording ? [1920] : [1366, 1920]) {
    await page.setViewportSize({ width, height: 960 }); await hold(500);
    assert.ok(await reader.locator('.pdf-reader-viewport').evaluate(el => el.scrollWidth <= el.clientWidth + 2));
  }
  report.checks.push(recording ? 'fit-width at 1920' : 'fit-width at 1366 and 1920 without horizontal PDF overflow');
  const exportLink = reader.getByRole('link', { name: 'HTML', exact: true });
  if (await exportLink.count()) {
    const [download] = await Promise.all([page.waitForEvent('download'), exportLink.click()]);
    await download.saveAs(path.join(output, 'translation.html'));
    report.checks.push('completed full-document HTML download');
  }
  report.paper = paper;
  report.source_blocks = [abstract.block_id, other.block_id];
  report.source_columns = [abstract.bbox[0], other.bbox[0]];
  assert.deepEqual(report.errors, []);
  report.status = 'passed';
} catch (error) {
  report.error = String(error); report.status = 'failed'; process.exitCode = 1;
  await page.screenshot({ path: path.join(output, 'failure.png') }).catch(() => {});
} finally {
  report.action_seconds = (Date.now() - start) / 1000;
  await context.close(); report.video = await page.video().path(); await browser.close();
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
}
