// Start an isolated frontend preview on 8875, then run this from the repository root.
// PLAYWRIGHT_MODULE and CHROME_PATH work as in capture-readme.mjs. All API data is synthetic.
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {apiResponse, jobs, now, snapshot} from './readme-demo-data.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(resolve(process.env.PLAYWRIGHT_MODULE)).href : 'playwright');
const base = process.env.JOBS_PREVIEW_URL || 'http://127.0.0.1:8875';
assert.ok(['http://127.0.0.1:8875', 'http://127.0.0.1:8876'].includes(base), 'Use an isolated preview port');
const out = 'run/preview-jobs-scroll';
await mkdir(out, {recursive: true});
const items = Array.from({length: 80}, (_, i) => ({...structuredClone(jobs[i % jobs.length]), job_key: `scroll-job-${i}`, task_name: `workload_${String(i).padStart(2, '0')}.py`}));
const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox']});
const reports = [], errors = [];
let release;
try {
  for (const [name, width, theme] of [['desktop-light', 1440, 'light'], ['desktop-dark', 1440, 'dark'], ['mobile', 390, 'light']]) {
    const context = await browser.newContext({viewport: {width, height: 1050}, colorScheme: theme});
    await context.addInitScript(({theme, now}) => {localStorage.setItem('constella.theme', theme); Date.now = () => now * 1000;}, {theme, now});
    let heldKey = '', gate, failKey = '';
    await context.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/highres/jobs') {
        const query = url.searchParams.get('q') || '';
        await route.fulfill({json: {enabled: true, items: items.filter(i => i.task_name.includes(query))}});
      } else if (url.pathname.startsWith('/api/highres/jobs/')) {
        const key = decodeURIComponent(url.pathname.split('/')[4]);
        if (key === heldKey) await gate;
        if (key === failKey) {await route.fulfill({status: 500, json: {error: 'isolated failure'}}); return;}
        const payload = apiResponse(url);
        await route.fulfill({json: {...payload, job: items.find(i => i.job_key === key)}});
      } else if (url.pathname === '/api/lab/me') {
        await route.fulfill({json: {user: {id: 'preview', email: 'preview@example.test', display_name: 'Preview', role: 'admin', status: 'active', onboarding_completed_at: now, bindings: []}}});
      } else await route.fulfill({json: apiResponse(url)});
    });
    await context.route('**/*', async route => new URL(route.request().url()).origin === base ? route.fallback() : route.abort());
    await context.routeWebSocket('**/ws/cluster', ws => ws.send(JSON.stringify(snapshot)));
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base + '/jobs');
    await page.locator('[data-job-chart] canvas').first().waitFor();
    await page.evaluate(() => document.fonts.ready);
    const card = await page.locator('.job-list-card').elementHandle();
    const selected = page.locator('[data-job-key="scroll-job-25"]');
    await selected.evaluate(button => {
      const list = button.closest('.job-list-card');
      list.scrollTop += button.getBoundingClientRect().top - list.getBoundingClientRect().top - 180;
    });
    await selected.scrollIntoViewIfNeeded();
    const originalButton = await selected.elementHandle();
    const top = await card.evaluate(el => el.scrollTop);
    assert.ok(top > 500, 'Exercise a genuinely scrolled list');
    async function unchanged(stage, focused = false) {
      assert.equal(await card.evaluate(el => el === document.querySelector('.job-list-card')), true, `${name}: list replaced during ${stage}`);
      assert.equal(await card.evaluate(el => el.scrollTop), top, `${name}: scroll changed during ${stage}`);
      if (focused) assert.equal(await originalButton.evaluate(el => el === document.activeElement), true, `${name}: focus lost during ${stage}`);
    }
    heldKey = 'scroll-job-25'; gate = new Promise(resolve => {release = resolve;});
    await selected.click();
    await page.getByText('loading job curve', {exact: true}).waitFor();
    await unchanged('loading', true);
    release(); heldKey = '';
    await page.locator('[data-job-chart] canvas').first().waitFor();
    await unchanged('response', true);
    assert.equal(await selected.getAttribute('aria-pressed'), 'true');
    await page.keyboard.press('Enter');
    await page.locator('[data-job-chart] canvas').first().waitFor();
    await unchanged('keyboard activation', true);
    await page.locator('[data-job-metric]').selectOption('avg_power_watts');
    await unchanged('metric change');
    await page.locator('[data-analytics-action="job-resolution"][data-resolution="2m"]').click();
    await page.locator('[data-job-chart] canvas').first().waitFor();
    await unchanged('resolution response');
    await page.locator('[data-analytics-action="job-gpu"][data-gpu-uuid]').first().click();
    await unchanged('GPU selection');
    await page.getByRole('button', {name: 'Expand job curve', exact: true}).click();
    await page.getByRole('dialog').waitFor();
    await unchanged('expand');
    await page.keyboard.press('Escape');
    await page.getByRole('dialog').waitFor({state: 'detached'});
    await unchanged('collapse');
    failKey = 'scroll-job-25';
    await selected.click();
    await page.getByText('no curve points are available for this job window', {exact: true}).waitFor();
    await unchanged('failed response', true);
    failKey = ''; await selected.click();
    await page.locator('[data-job-chart] canvas').first().waitFor();
    await unchanged('retry', true);
    // A late response for the previous selection must not change either list or detail.
    heldKey = 'scroll-job-25'; gate = new Promise(resolve => {release = resolve;});
    await selected.click();
    await page.locator('[data-job-key="scroll-job-26"]').click();
    await page.locator('[data-job-chart] canvas').first().waitFor();
    release(); heldKey = ''; await page.waitForTimeout(150);
    await unchanged('out-of-order response');
    assert.match(await page.locator('.job-curve-card .card-title').innerText(), /workload_26/);
    assert.equal(await page.locator('.job-list-item.is-active').getAttribute('data-job-key'), 'scroll-job-26');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path: `${out}/${name}.png`});
    await page.locator('[data-job-query]').fill('workload_0');
    await page.getByRole('button', {name: 'Search jobs', exact: true}).click();
    await page.waitForFunction(() => document.querySelectorAll('.job-list-item').length === 10);
    assert.equal(await card.evaluate(el => el.isConnected), false, 'New search should render its new result set');
    assert.equal(await page.locator('.job-list-card').evaluate(el => el.scrollTop), 0);
    reports.push({name, scrollTop: top, checks: ['loading', 'response', 'focus', 'keyboard', 'metric', 'resolution', 'GPU', 'expand/collapse', 'failure/retry', 'out-of-order', 'new search']});
    await context.close();
  }
  assert.deepEqual(errors, []);
} finally {
  release?.();
  await browser.close();
  await writeFile(`${out}/verification.json`, JSON.stringify({reports, errors}, null, 2));
}
console.log(JSON.stringify({reports, errors}, null, 2));
