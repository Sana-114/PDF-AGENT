// Actual application flows in the isolated public recording library.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright-core';
import { waitForPaintedPdfPage } from './pdfRenderReady.mjs';
const root = path.resolve('../backend/tmp/demo-final-20261110');
const selected = process.env.PDFAGENT_SCENE || 'writing';
const take = process.env.PDFAGENT_TAKE || 'take1';
const folder = path.join(root, take, selected);
await mkdir(folder, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 960 },
  permissions: ['clipboard-read', 'clipboard-write'], recordVideo: { dir: path.join(folder, 'raw'), size: { width: 1920, height: 960 } } });
const p = await context.newPage(); p.setDefaultTimeout(45000);
const report = { status: 'started', errors: [], checks: [], mocked_responses: false };
p.on('pageerror', e => report.errors.push(e.message));
const hold = ms => p.waitForTimeout(ms);
const start = Date.now();
try {
  const docs = (await (await p.request.get('http://127.0.0.1:8024/api/v1/documents?limit=200')).json()).items;
  await p.goto('http://127.0.0.1:3344/', { waitUntil: 'networkidle' });
  report.ui_ready_seconds = (Date.now() - start) / 1000;
  if (selected === 'writing') {
    await p.getByLabel('工作区导航').getByRole('button', { name: /研究产出/ }).click();
    await p.getByRole('button', { name: '论文框架', exact: true }).click();
    const s = p.locator('#writing-workbench');
    await s.getByLabel('研究主题', { exact: true }).fill('低显存条件下的语言模型适配');
    await s.getByLabel('写作要求', { exact: true }).fill('整理 LoRA 和 QLoRA 的相关工作，说明冻结主模型、训练低秩适配器的共同机制，以及量化带来的区别。只写原文支持的事实，尚未开展新实验。');
    await s.locator('.document-scope-picker summary').click();
    const picked = docs.filter(d => ['2106.09685v2.pdf', '2305.14314v1.pdf'].includes(d.original_filename));
    assert.equal(picked.length, 2);
    for (const doc of picked) await s.locator(`[data-document-id="${doc.id}"] input`).check();
    await s.locator('.document-scope-picker summary').click(); await hold(2500);
    const promise = p.waitForResponse(r => r.url().endsWith('/writing/outline') && r.request().method() === 'POST', { timeout: 240000 });
    await s.getByRole('button', { name: '整理论文框架', exact: true }).click();
    const response = await promise; report.result = await response.json();
    await writeFile(path.join(folder, 'response.json'), JSON.stringify(report.result, null, 2));
    assert.equal(response.status(), 200); assert.equal(report.result.provider, 'deepseek');
    assert.equal(report.result.insufficient_evidence, false);
    await s.locator('.writing-workbench').scrollIntoViewIfNeeded(); await hold(5000);
    await s.getByRole('button', { name: '复制框架 Markdown', exact: true }).click();
    const copied = await p.evaluate(() => navigator.clipboard.readText());
    assert.ok(copied.includes('References') && copied.includes('低显存条件下'));
    await writeFile(path.join(folder, 'outline.md'), copied);
    const anchor = report.result.evidence.find(e => e.evidence_id === report.result.claims[0].evidence_ids[0]);
    await s.getByRole('button', { name: `${anchor.evidence_id} · p.${anchor.page_number}`, exact: true }).first().click();
    await waitForPaintedPdfPage(p.getByRole('dialog'), anchor.page_number); await hold(5000);
    await p.getByRole('button', { name: '关闭阅读器', exact: true }).click();
    report.checks.push('real generation, three sections, original page, clipboard with references');
  } else if (selected === 'comparison') {
    await p.getByLabel('工作区导航').getByRole('button', { name: /文献问答/ }).click();
    await p.getByRole('button', { name: '多文对比', exact: true }).click();
    const s = p.locator('.comparison-workbench');
    const picked = docs.filter(d => d.original_filename === 'N19-1423.pdf' || d.arxiv_id === '1706.03762' && d.arxiv_version === 1);
    assert.equal(picked.length, 2);
    for (const doc of picked) await s.locator(`[data-document-id="${doc.id}"] input`).check();
    for (const doc of docs.filter(d => !picked.some(item => item.id === d.id))) {
      const box = s.locator(`[data-document-id="${doc.id}"] input`);
      if (await box.count() && await box.isChecked()) await box.uncheck();
    }
    await s.getByLabel('对比维度或问题').fill('BERT 使用的 Transformer encoder 与原始 Transformer 的 encoder-decoder 架构有什么关系？请分别引用两篇原文。');
    const promise = p.waitForResponse(r => r.url().endsWith('/agent/compare') && r.request().method() === 'POST', { timeout: 240000 });
    await s.getByRole('button', { name: '开始证据对比', exact: true }).click();
    const response = await promise; report.result = await response.json();
    await writeFile(path.join(folder, 'response.json'), JSON.stringify(report.result, null, 2));
    assert.equal(response.status(), 200); assert.equal(report.result.provider, 'deepseek');
    await s.locator('.comparison-result').scrollIntoViewIfNeeded(); await hold(5000);
    for (const doc of picked) {
      const anchor = report.result.evidence.find(e => e.document_id === doc.id && s);
      const buttons = s.getByRole('button', { name: `${anchor.evidence_id} · p.${anchor.page_number}`, exact: true });
      await buttons.first().click(); await waitForPaintedPdfPage(p.getByRole('dialog'), anchor.page_number); await hold(4000);
      await p.getByRole('button', { name: '关闭阅读器', exact: true }).click();
    }
    report.checks.push('live cross-paper request and two original pages');
  } else if (selected === 'upload') {
    await p.getByLabel('工作区导航').getByRole('button', { name: /文献库/ }).click();
    const doc = docs.find(d => d.arxiv_id === '1706.03762' && d.arxiv_version === 7);
    const original = await p.request.get(`http://127.0.0.1:8024/api/v1/documents/${doc.id}/file`);
    assert.ok(original.ok());
    const bytes = await original.body();
    const promise = p.waitForResponse(r => r.url().endsWith('/documents') && r.request().method() === 'POST');
    await p.locator('.dropzone input[type=file]').setInputFiles({ name: 'renamed-transformer.pdf', mimeType: 'application/pdf', buffer: bytes });
    const response = await promise; report.result = await response.json();
    assert.equal(report.result.exact_duplicate, true); await hold(6000);
    report.checks.push('renamed byte-identical upload recognized without inserting another document');
  } else if (selected === 'discovery') {
    await p.getByLabel('工作区导航').getByRole('button', { name: /查找论文/ }).click();
    await p.getByLabel('论文题名、DOI 或 arXiv ID').fill('1706.03762');
    await p.getByRole('button', { name: '检索论文', exact: true }).click();
    await p.locator('.discovery-card').first().waitFor({ timeout: 120000 });
    assert.match(await p.locator('.discovery-results').innerText(), /Attention Is All You Need/i);
    await hold(8000); report.checks.push('actual online search displays matching paper result');
  }
  await p.screenshot({ path: path.join(folder, 'result.png') });
  assert.deepEqual(report.errors, []); report.status = 'captured_pending_media_review';
} catch (error) { report.status = 'failed'; report.errors.push(String(error)); process.exitCode = 1;
  await p.screenshot({ path: path.join(folder, 'failure.png') }).catch(() => {});
} finally {
  report.action_seconds = (Date.now() - start) / 1000;
  await context.close(); report.video = await p.video().path(); await browser.close();
  await writeFile(path.join(folder, 'capture.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ status: report.status, checks: report.checks, errors: report.errors, seconds: report.action_seconds }));
}
