// Capture the editorial closing card; this is not a product feature demonstration.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';

const root = path.resolve('../backend/tmp/subtitled-demo-2026-10-27');
const output = path.join(root, 'take1/closing');
await mkdir(output, { recursive: true });
try { await readFile(path.join(output, 'capture.json')); throw new Error('Take already exists'); }
catch (e) { if (e.code !== 'ENOENT') throw e; }
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 960 },
  recordVideo: { dir: path.join(output, 'raw'), size: { width: 1920, height: 960 } } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
await page.goto(pathToFileURL(path.join(root, 'status-card.html')).href);
await page.getByRole('heading', { name: '从阅读论文，到整理研究材料' }).waitFor();
await page.screenshot({ path: path.join(output, 'result.png') });
await page.waitForTimeout(12000);
await context.close();
const video = await page.video().path();
await browser.close();
assert.deepEqual(errors, []);
await writeFile(path.join(output, 'capture.json'), JSON.stringify({
  id: 'closing', status: 'captured_pending_media_review', editorial_card: true,
  mocked_responses: false, cloud_calls: 0, errors, action_seconds: 12, video,
}, null, 2));
console.log('Closing card captured; no application or model request');
