// Run from the repository root; see docs/assets/README.md for prerequisites.
import {mkdir, writeFile, readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {apiResponse, snapshot, now} from './readme-demo-data.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(resolve(process.env.PLAYWRIGHT_MODULE)).href : 'playwright');
const base = 'http://127.0.0.1:8875';
const out = 'run/preview-readme';
await mkdir(out, {recursive: true});
const browser = await chromium.launch({headless: true, ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {}), args: ['--no-sandbox']});
const errors = [];
const cases = [
  {name: '01-cluster-overview', route: '/overview', first: '.summary-grid', last: '.fabric-band'},
  {name: '02-node-detail', route: '/nodes/demo-train-01', first: '.gpu-grid', last: '.process-section'},
  {name: '03-performance', route: '/performance?node=demo-train-01&gpu=DEMO-0-0', first: '.performance-toolbar', last: '.group-compute'},
  {name: '04-job-search', route: '/jobs', first: '.jobs-section', last: '.jobs-section'},
];
const report = [];
try {
  for (const theme of ['light', 'dark', 'mobile']) {
    const context = await browser.newContext({viewport: {width: theme === 'mobile' ? 390 : 1440, height: 1100}, deviceScaleFactor: 2, timezoneId: 'Asia/Shanghai', colorScheme: theme === 'dark' ? 'dark' : 'light'});
    await context.addInitScript(({theme, now}) => {
      localStorage.setItem('constella.theme', theme === 'dark' ? 'dark' : 'light');
      Date.now = () => now * 1000;
      localStorage.setItem('constella.performance.collapsed', JSON.stringify(['chart:nvidia.gpm.tensor_active', 'chart:nvidia.gpm.sm_occupancy', 'group:memory', 'group:interconnect', 'group:pipelines']));
    }, {theme, now});
    // No production connection: HTTP APIs and live WebSocket are intercepted.
    await context.route('**/api/**', async route => {
      try { await route.fulfill({json: apiResponse(new URL(route.request().url()))}); }
      catch (error) { errors.push(error.message); await route.abort(); }
    });
    await context.route('**/*', async route => {
      if (new URL(route.request().url()).origin !== base) {errors.push('Blocked external request'); await route.abort();}
      else await route.fallback();
    });
    await context.routeWebSocket('**/ws/cluster', ws => { ws.send(JSON.stringify(snapshot)); });
    for (const c of cases) {
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + c.route);
      await page.locator('.fabric-node-card').first().waitFor({state: 'attached'});
      await page.evaluate(() => document.fonts.ready);
      if (c.name === '04-job-search') {
        await page.locator('[data-job-query]').fill('train');
        await page.getByRole('button', {name: 'Search jobs', exact: true}).click();
        await page.locator('[data-job-chart] canvas').first().waitFor();
      }
      if (c.name === '03-performance') {
        await page.getByRole('button', {name: 'GPU1', exact: true}).click();
        await page.locator('.group-compute canvas').first().waitFor();
      }
      await page.waitForTimeout(500);
      if (c.name === '04-job-search' && theme !== 'mobile') {
        const plot = await page.locator('[data-job-chart] .u-over').boundingBox();
        await page.mouse.move(plot.x + plot.width * .61, plot.y + plot.height * .25);
      }
      await page.screenshot({path: `${out}/${c.name}-${theme}-full.png`, fullPage: true});
      report.push({page: c.name, theme, overflow: await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)});
      if (theme === 'light') {
        const first = await page.locator(c.first).boundingBox();
        const last = await page.locator(c.last).boundingBox();
        if (!first || !last) throw new Error(`Missing screenshot region: ${c.name}`);
        const paddingY = c.name === '03-performance' ? 4 : 16;
        const clip = {x: Math.max(0, first.x - 16), y: Math.max(0, first.y - paddingY), width: first.width + 32, height: last.y + last.height - first.y + paddingY * 2};
        await page.setViewportSize({width: 1440, height: Math.max(1100, Math.ceil(clip.y + clip.height))});
        await page.screenshot({path: `docs/assets/${c.name}.png`, clip});
      }
      await page.close();
    }
    await context.close();
  }
} finally {await browser.close();}
await writeFile(`${out}/verification.json`, JSON.stringify({report, errors}, null, 2));
if (errors.length) throw new Error(errors.join('\n'));
console.log(JSON.stringify(report, null, 2));

const files = await Promise.all(cases.map(async c => {
  const png = await readFile(`docs/assets/${c.name}.png`);
  return {file: `${c.name}.png`, width: png.readUInt32BE(16), height: png.readUInt32BE(20), bytes: png.length};
}));
await writeFile('docs/assets/asset-manifest.json', JSON.stringify({dataset_time: new Date(now * 1000).toISOString(), base_url: base, viewport_css_px: '1440x1100 (expanded vertically for crop when needed)', device_scale_factor: 2, theme: 'light', files}, null, 2) + '\n');
await writeFile('docs/assets/review-report.json', JSON.stringify({ok: errors.length === 0 && report.every(r => !r.overflow), checks: report, errors, assets: files, data_source: 'scripts/dev/readme-demo-data.mjs', reproduction_production_access: false, performance_source: 'Read-only production API capture, identifiers replaced and timestamps shifted; original numeric samples preserved'}, null, 2) + '\n');
