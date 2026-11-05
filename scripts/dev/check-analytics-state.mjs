// Start a frontend preview on 8875/8876; run from the repository root.
// Uses the same optional PLAYWRIGHT_MODULE / CHROME_PATH settings as capture-readme.mjs.
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {apiResponse, jobs, now, snapshot} from './readme-demo-data.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(resolve(process.env.PLAYWRIGHT_MODULE)).href : 'playwright');
const base = process.env.JOBS_PREVIEW_URL || 'http://127.0.0.1:8875';
assert.ok(['http://127.0.0.1:8875', 'http://127.0.0.1:8876'].includes(base));
const out = 'run/preview-jobs-scroll';
await mkdir(out, {recursive: true});
const items = Array.from({length: 80}, (_, i) => ({...structuredClone(jobs[i % jobs.length]), job_key: `audit-job-${i}`, task_name: `audit_task_${i}.py`}));
const seed = apiResponse(new URL(base + '/api/highres/jobs/demo-job-0/gpu'));
const series = Array.from({length: 24}, (_, i) => ({...structuredClone(seed.series[0]), gpu_uuid: `audit-gpu-${i}`, gpu_index: i, label: `demo-train-01 GPU${i}`, points: Array.from({length: 1200}, (_, j) => ({...seed.series[0].points[j % 361], bucket_start: now - 24000 + j * 20}))}));
const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox']});
const reports = [], errors = [];
const settled = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
async function same(handle, selector, page) {
  assert.equal(await handle.evaluate((el, selector) => el === document.querySelector(selector), selector), true, `${selector} must retain its DOM node`);
}
try {
  for (const [name, width, theme] of [['desktop-light', 1440, 'light'], ['desktop-dark', 1440, 'dark'], ['mobile', 390, 'light']]) {
    const context = await browser.newContext({viewport: {width, height: 1100}, colorScheme: theme});
    await context.addInitScript(({now, theme}) => {Date.now = () => now * 1000; localStorage.setItem('constella.theme', theme);}, {now, theme});
    await context.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      let payload;
      if (url.pathname === '/api/highres/jobs') payload = {enabled: true, items};
      else if (url.pathname.startsWith('/api/highres/jobs/')) {
        payload = {...seed, job: items.find(i => url.pathname.split('/').includes(i.job_key)) || items[0], series};
        if (url.searchParams.get('resolution') === '2m') payload = {...payload, series: series.map(s => ({...s, points: s.points.filter((_, i) => i % 6 === 0)}))};
      } else if (url.pathname.startsWith('/api/analytics/node/')) payload = {enabled: true, range_start: now - 24000, range_end: now, series, heatmap: []};
      else if (url.pathname === '/api/lab/me') payload = {user: {id: 'preview', email: 'preview@example.test', display_name: 'Preview', role: 'admin', status: 'active', onboarding_completed_at: now, bindings: []}};
      else payload = apiResponse(url);
      await route.fulfill({json: payload});
    });
    await context.route('**/*', async route => new URL(route.request().url()).origin === base ? route.fallback() : route.abort());
    await context.routeWebSocket('**/ws/cluster', ws => ws.send(JSON.stringify(snapshot)));
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base + '/jobs');
    await page.locator('[data-job-chart] canvas').first().waitFor();
    await page.evaluate(() => document.fonts.ready);
    const card = await page.locator('.job-list-card').elementHandle();
    await card.evaluate(el => el.scrollTop = 1200);
    const listTop = await card.evaluate(el => el.scrollTop);
    await page.locator('[data-job-query]').fill('unsubmitted draft');
    const refreshed = page.waitForResponse(r => new URL(r.url()).pathname === '/api/highres/jobs');
    await page.getByRole('button', {name: 'Refresh jobs', exact: true}).click();
    await refreshed; await settled(page);
    await same(card, '.job-list-card', page);
    assert.equal(await card.evaluate(el => el.scrollTop), listTop);
    assert.equal(await page.locator('[data-job-query]').inputValue(), 'unsubmitted draft');
    await page.locator('[data-job-chart] canvas').first().waitFor();
    const gpu = page.locator('[data-analytics-action="job-gpu"][data-gpu-uuid="audit-gpu-23"]');
    await gpu.scrollIntoViewIfNeeded();
    const legend = await page.locator('.job-curve-card .chart-legend').elementHandle();
    const legendLeft = await legend.evaluate(el => el.scrollLeft);
    await gpu.click();
    await same(legend, '.job-curve-card .chart-legend', page);
    assert.equal(await legend.evaluate(el => el.scrollLeft), legendLeft);
    assert.equal(await gpu.evaluate(el => el === document.activeElement), true);
    const plot = await page.locator('.job-curve-card .chart-scroll').elementHandle();
    await plot.evaluate(el => el.scrollLeft = 300);
    await page.locator('[data-job-metric]').selectOption('avg_power_watts');
    await same(plot, '.job-curve-card .chart-scroll', page);
    assert.equal(await plot.evaluate(el => el.scrollLeft), 300);
    // A temporary loading panel and new point density must retain the same relative time window.
    const fraction = await plot.evaluate(el => el.scrollLeft / (el.scrollWidth - el.clientWidth));
    await page.locator('[data-analytics-action="job-resolution"][data-resolution="2m"]').click();
    await page.locator('[data-job-chart] canvas').first().waitFor();
    const newFraction = await page.locator('.job-curve-card .chart-scroll').evaluate(el => el.scrollLeft / (el.scrollWidth - el.clientWidth || 1));
    if (width < 760) assert.ok(Math.abs(fraction - newFraction) < .02);
    const expand = page.getByRole('button', {name: 'Expand job curve', exact: true});
    await expand.click();
    assert.equal(await page.getByRole('button', {name: 'Close expanded curve', exact: true}).evaluate(el => el === document.activeElement), true);
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.getByRole('dialog').evaluate(el => el.contains(document.activeElement)), true);
    await page.keyboard.press('Escape');
    await page.getByRole('dialog').waitFor({state: 'detached'});
    assert.equal(await expand.evaluate(el => el === document.activeElement), true);
    await page.goto(base + '/nodes/demo-train-01');
    await page.locator('[data-node-chart] canvas').first().waitFor();
    const metric = await page.locator('[data-analytics-action="node-metric"][data-metric="avg_power_watts"]').elementHandle();
    await metric.click();
    assert.equal(await metric.evaluate(el => el.isConnected && el === document.activeElement), true);
    const range = await page.locator('[data-analytics-action="node-range"][data-range="7d"]').elementHandle();
    await range.click(); await page.locator('[data-node-chart] canvas').first().waitFor();
    assert.equal(await range.evaluate(el => el.isConnected && el === document.activeElement), true);
    await page.goto(base + '/overview');
    await page.locator('.analytics-table-wrap').first().waitFor();
    const table = await page.locator('.analytics-table-wrap').first().elementHandle();
    await table.evaluate(el => el.scrollLeft = 100);
    const tableLeft = await table.evaluate(el => el.scrollLeft);
    const overviewResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/analytics/overview');
    await page.getByRole('button', {name: 'Refresh analytics', exact: true}).click();
    await overviewResponse; await settled(page);
    await same(table, '.analytics-table-wrap', page);
    assert.equal(await table.evaluate(el => el.scrollLeft), tableLeft);
    if (width < 760) assert.equal(tableLeft, 100);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path: `${out}/analytics-${name}.png`});
    reports.push({name, listTop, legendLeft, chartLeft: 300, tableLeft, focusAndEscape: 'passed'});
    await context.close();
  }
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
  await writeFile(`${out}/analytics-state-verification.json`, JSON.stringify({reports, errors}, null, 2));
}
console.log(JSON.stringify({reports, errors}, null, 2));
