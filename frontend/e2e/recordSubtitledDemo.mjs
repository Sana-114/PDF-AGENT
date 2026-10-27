// Record real local UI clips. Never inject API results or synthesize an answer.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright-core';
import { waitForPaintedPdfPage } from './pdfRenderReady.mjs';

const repo = path.resolve('..');
const root = path.join(repo, 'backend/tmp/subtitled-demo-2026-10-26');
const timeline = JSON.parse(await readFile(path.join(root, 'timeline.json'), 'utf8'));
const rows = JSON.parse(await readFile(path.join(repo, 'backend/tmp/official-recording-release-2026-10-24/data/fixture.json'), 'utf8'));
const oldTurn = JSON.parse(await readFile(path.join(root, 'live-rag/turn.json'), 'utf8'));
const selected = process.env.PDFAGENT_RECORD_SCENES?.split(',');
const take = process.env.PDFAGENT_RECORD_TAKE || 'take1';
assert.match(take, /^[a-z0-9-]+$/);
const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] });
const base = 'http://127.0.0.1:3344';
const hold = (p, ms = 2500) => p.waitForTimeout(ms);
async function library(p) {
  await p.getByLabel('工作区导航').getByRole('button', { name: /文献库/ }).click();
}
async function reader(p) {
  await library(p);
  const card = p.locator('.document-card').filter({ hasText: 'arXiv:1706.03762v1' }).first();
  await card.getByRole('button', { name: '在线阅读', exact: true }).click();
  const r = p.getByRole('dialog', { name: /阅读/ });
  await waitForPaintedPdfPage(r, 1);
  return r;
}
async function scope(p, id, papers) {
  const section = p.locator(id);
  await section.locator('.document-scope-picker > summary').click();
  for (const row of papers) await section.locator(`label[data-document-id="${row.document_id}"] input`).check();
  await section.locator('.document-scope-picker > summary').click();
  return section;
}
const handlers = {
  async overview(p, r) {
    await hold(p, 5000); await library(p); await hold(p, 5000);
    const cards = p.locator('.document-card');
    assert.ok(await cards.count() >= rows.length);
    r.checks.push(`public library rendered: ${await cards.count()} cards; graph scope remains fixture's 20`);
    await cards.nth(3).scrollIntoViewIfNeeded(); await hold(p, 5000);
    await cards.first().scrollIntoViewIfNeeded();
  },
  async reader(p, report) {
    const r = await reader(p); await hold(p, 5000);
    await r.getByRole('button', { name: /3 Model Architecture/ }).click();
    await waitForPaintedPdfPage(r, 2); await hold(p, 5000);
    const link = r.locator('.pdf-citation-link').first();
    const label = await link.getAttribute('data-reference-label');
    await link.click();
    await r.getByRole('button', { name: `返回正文 [${label}]`, exact: true }).waitFor();
    await waitForPaintedPdfPage(r, Number(await r.locator('#pdf-page-number').inputValue()));
    await hold(p, 5000);
    await r.getByRole('button', { name: `返回正文 [${label}]`, exact: true }).click();
    await waitForPaintedPdfPage(r, 2);
    report.checks.push('rendered outline and actual citation round trip');
  },
  async qa(p, r) {
    await p.getByLabel('工作区导航').getByRole('button', { name: /文献问答/ }).click();
    await p.getByRole('button', { name: '连续问答', exact: true }).click();
    const restored=p.waitForResponse(response=>response.url().endsWith(`/agent/conversations/${oldTurn.conversation.id}`)&&response.status()===200);
    await p.locator('.chat-history-item').filter({ hasText: oldTurn.conversation.title }).getByRole('button').first().click();
    await restored;
    const claims = p.locator('.chat-message.assistant .chat-claims');
    await claims.waitFor(); assert.match(await claims.innerText(), /P100/);
    await hold(p, 14000);
    const anchor = oldTurn.assistant_message.evidence.find(e => e.page_number === 7 && e.quote.includes('P100') && e.quote.includes('5.2'));
    await claims.getByRole('button', { name: `${anchor.evidence_id} · p.7`, exact: true }).first().click();
    await waitForPaintedPdfPage(p.getByRole('dialog'), 7);
    r.checks.push('saved genuine conversation restored, page 7 painted; no new generation');
  },
  async bilingual(p, report) {
    const r = await reader(p);
    await r.getByRole('button', { name: '中英', exact: true }).click();
    await r.locator('.pdf-bilingual-segments article').first().waitFor();
    assert.match(await r.locator('.pdf-translation-job').innerText(), /15\/15 页/);
    await hold(p, 10000);
    for (let i=0; i<4; i++) await r.getByRole('button', { name: '放大', exact: true }).click();
    for (const [selector, wanted] of [['.pdf-reader-viewport', .55], ['.pdf-bilingual-pane', .25]]) {
      await r.locator(selector).evaluate((el, f) => { el.scrollTop = (el.scrollHeight-el.clientHeight)*f; }, wanted);
      await p.waitForFunction(f => ['.pdf-reader-viewport','.pdf-bilingual-pane'].every(s => {
        const el=document.querySelector(s); return el.scrollHeight-el.clientHeight>50 && Math.abs(el.scrollTop/(el.scrollHeight-el.clientHeight)-f)<.05;
      }), wanted);
      await hold(p, 5000);
    }
    report.checks.push('15/15 current translation visible; both scroll directions synchronized');
  },
  async graph(p, report) {
    await p.getByLabel('工作区导航').getByRole('button', { name: /研究脉络/ }).click();
    const s=await scope(p, '#citation-graph', rows);
    const reply=p.waitForResponse(r=>r.url().endsWith('/citation-graph') && r.request().method()==='POST');
    await s.getByRole('button',{name:'计算图谱',exact:true}).click();
    const response=await reply; assert.equal(response.status(),200);
    const graph=await response.json(); report.result=graph;
    const svg=s.getByRole('img',{name:'局域引用关系图'}); await svg.waitFor();
    await svg.scrollIntoViewIfNeeded(); await hold(p,10000);
    await svg.getByRole('button',{name:/Attention Is All You Need/}).click();
    await hold(p,5000); await s.getByRole('button',{name:'打开原文',exact:true}).click();
    await waitForPaintedPdfPage(p.getByRole('dialog'),1);
    report.checks.push('live graph response and selected original page painted');
  },
  async future(p, report) {
    await p.getByLabel('工作区导航').getByRole('button',{name:/研究脉络/}).click();
    await p.getByRole('button',{name:'证据综述',exact:true}).click();
    const names=['2005.14165v4.pdf','2203.02155v1.pdf','1706.03762v1.pdf','2010.11929v2.pdf','2106.09685v2.pdf','2305.18290v3.pdf'];
    const s=await scope(p,'#research-review',names.map(n=>rows.find(r=>r.filename===n)));
    await s.getByLabel('仅整理原文（不调用 LLM）').check();
    const promise=p.waitForResponse(r=>r.url().endsWith('/reviews/generate')&&r.status()===200);
    await s.getByRole('button',{name:'整理综述',exact:true}).click();
    const result=await (await promise).json(); report.result=result;
    assert.ok(result.future_directions.some(d=>d.assessment?.excluded_from_recommendations));
    const excluded=s.locator('details.future-confirmed').filter({hasText:'原计划已有完成证据'});
    await excluded.locator(':scope > summary').click();
    const card=excluded.locator('.future-card').filter({hasText:'GPT-3 can be'}).first();
    await card.locator('.future-decision-editor > summary').click();
    await card.scrollIntoViewIfNeeded(); await hold(p,14000);
    await card.getByRole('button',{name:/后续原文 · p\./}).first().click();
    await waitForPaintedPdfPage(p.getByRole('dialog'),6); await hold(p,8000);
    await p.getByRole('button',{name:'关闭阅读器'}).click();
    report.checks.push('cached approved goal retained with exact later source; no new cloud review');
  },
  async charts(p, report) {
    await p.getByLabel('工作区导航').getByRole('button',{name:/研究产出/}).click();
    await p.getByRole('button',{name:'数据作图',exact:true}).click();
    const s=p.locator('.data-visualization-workbench');
    await s.locator('input[type="file"]').setInputFiles({name:'synthetic-demo.csv',mimeType:'text/csv',buffer:Buffer.from('配置,精度指标,速度指标,覆盖指标\n配置甲,72,85,68\n配置乙,81,73,88\n配置丙,87,66,79\n')});
    for(const type of ['line','bar','radar3d']) {
      await s.getByLabel('图表类型').selectOption(type);
      const promise=p.waitForResponse(r=>r.url().includes('/visualizations/analyze')&&r.request().method()==='POST');
      await s.getByRole('button',{name:'绘制图表',exact:true}).click();
      const response=await promise; assert.equal(response.status(),200);
      (report.results??=[]).push(await response.json());
      await s.locator('.scientific-chart-svg').waitFor();
      await s.locator('.scientific-figure-card').scrollIntoViewIfNeeded(); await hold(p,6000);
    }
    report.checks.push('synthetic CSV rendered as three live chart responses');
  },
  async diagram(p, report) {
    await p.getByLabel('工作区导航').getByRole('button',{name:/研究产出/}).click();
    await p.getByRole('button',{name:'架构图',exact:true}).click();
    const s=p.locator('.architecture-diagram-workbench');
    await hold(p,4000); await s.getByRole('button',{name:'整理四种脚本',exact:true}).click();
    await s.locator('.architecture-preview-svg').waitFor();
    for(const name of ['Mermaid','Graphviz','TikZ','Matplotlib']) {
      await s.getByRole('button',{name,exact:true}).click();
      await s.locator('.diagram-format-tabs').scrollIntoViewIfNeeded(); await hold(p,4000);
    }
    report.checks.push('four script tabs and live architecture preview rendered');
  },
  async discovery(p, report) {
    await p.getByLabel('工作区导航').getByRole('button',{name:/查找论文/}).click();
    await p.getByPlaceholder('例如：Attention Is All You Need / 1706.03762v7').fill('Attention Is All You Need');
    await hold(p,7000);
    await p.getByRole('button',{name:'动态追踪',exact:true}).click();
    const field=p.getByPlaceholder('例如：多模态 RAG'); await field.scrollIntoViewIfNeeded();
    await hold(p,7000); await p.evaluate(()=>window.scrollTo(0,0));
    report.checks.push('configuration entrance only; no claim of new external search');
  },
  async closing(p, report) {
    await p.goto('file:///'+path.join(root,'status-card.html').replaceAll('\\','/'));
    report.checks.push('editorial status card explicitly separate from product UI');
  }
};
try {
for (const scene of timeline.filter(s=>!selected || selected.includes(s.id))) {
  const folder=path.join(root,take,scene.id); await mkdir(folder,{recursive:true});
  const reportFile=path.join(folder,'capture.json');
  try { await readFile(reportFile); throw new Error('Existing take cannot be overwritten'); } catch(e) { if(e.code!=='ENOENT') throw e; }
  const context=await browser.newContext({viewport:{width:1920,height:960},recordVideo:{dir:path.join(folder,'raw'),size:{width:1920,height:960}}});
  const p=await context.newPage(); p.setDefaultTimeout(45000);
  const report={id:scene.id,status:'started',mocked_responses:false,cloud_calls:0,checks:[],errors:[],requests:[]};
  p.on('pageerror',e=>report.errors.push(e.message));
  await p.route('**/api/v1/**',async route=>{
    const req=route.request(),pathname=new URL(req.url()).pathname;
    report.requests.push({method:req.method(),path:pathname});
    const ok=req.method()==='GET'||(req.method()==='POST'&&(
      pathname==='/api/v1/citation-graph'||pathname==='/api/v1/visualizations/analyze'||pathname.includes('/diagrams')||
      (pathname==='/api/v1/reviews/generate'&&req.postDataJSON().generation_mode==='extractive')||/\/translations\/pages\/\d+$/.test(pathname)));
    if(!ok){report.errors.push('Prevented unexpected write: '+pathname);return route.abort();}
    return route.continue();
  });
  const start=Date.now();
  try {
    await p.goto(base,{waitUntil:'networkidle'});
    report.ui_ready_seconds=(Date.now()-start)/1000;
    await handlers[scene.id](p,report);
    await p.screenshot({path:path.join(folder,'result.png')});
    const elapsed=(Date.now()-start)/1000;
    report.action_seconds=elapsed;
    if(elapsed>scene.duration) report.edit_note='Actions exceed narration; trim only final hold after review';
    await hold(p,Math.max(1000,(scene.duration-elapsed)*1000));
    assert.deepEqual(report.errors,[]);
    report.status='captured_pending_media_review';
  }catch(e){report.status='failed';report.errors.push(String(e));await p.screenshot({path:path.join(folder,'failure.png')}).catch(()=>{});}
  finally{
    await context.close();report.video=await p.video().path();
    report.wall_seconds=(Date.now()-start)/1000;
    await writeFile(reportFile,JSON.stringify(report,null,2));
    console.log(JSON.stringify({id:scene.id,status:report.status,seconds:report.wall_seconds,errors:report.errors}));
  }
}
} finally { await browser.close(); }
