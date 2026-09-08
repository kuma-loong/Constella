const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function environment(overrides = {}) {
  const modules = new Map(), timers = new Map();
  let timerId = 0;
  const events = () => {
    const listeners = new Map();
    return {
      addEventListener: (type, fn) => { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
      removeEventListener: (type, fn) => listeners.get(type)?.delete(fn),
      emit: (type, value = {}) => { for (const fn of listeners.get(type) || []) fn(value); },
    };
  };
  const window = { ...events(), setTimeout: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; }, clearTimeout: id => timers.delete(id), setInterval: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; }, clearInterval: id => timers.delete(id) };
  const document = { ...events(), hidden: false };
  const sockets = [];
  class WebSocket {
    constructor() { Object.assign(this, events()); sockets.push(this); }
    close() { this.closed = true; this.emit('close', { code: 1000 }); }
  }
  const globals = { console, URLSearchParams, AbortController, Date, Intl, window, document, navigator: { onLine: true }, location: { protocol: 'http:', host: 'test' }, WebSocket, ...overrides };
  function load(name) {
    if (modules.has(name)) return modules.get(name);
    const exports = {};
    modules.set(name, exports);
    const require = dep => dep === 'uplot' ? class {} : dep.endsWith('.css') ? {} : load(dep.replace('./', ''));
    const source = fs.readFileSync(path.join(__dirname, '../src', name + '.ts'), 'utf8');
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    vm.runInNewContext(code, { ...globals, exports, require });
    return exports;
  }
  return { load, timers, window, document, sockets };
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const element = () => ({ innerHTML: '', querySelector: () => null, querySelectorAll: () => [] });
const payload = node => ({ enabled: true, node_id: node, range_end: Date.now() / 1000, series: [], heatmap: [{ gpu_index: 0, buckets: [{ bucket_start: Math.floor(Date.now() / 3_600_000) * 3600, sample_count: 10, avg_gpu_utilization: 80 }] }] });
const response = data => ({ ok: true, json: async () => data });
function controller(env) {
  const node = element();
  let route = { kind: 'node', nodeId: 'A' };
  const c = env.load('analytics').createAnalyticsController({ overviewElement: element(), nodeElement: node, jobElement: element(), currentRoute: () => route, renderIcons: () => {} });
  return { c, node, setRoute: value => { route = value; }, route: () => route };
}

test('24h deduplicates requests; failed heatmap is retried by history refresh', async () => {
  let failing = true, calls = 0;
  const env = environment({ fetch: async () => { calls++; return failing ? { ok: false, status: 500, json: async () => ({ error: "disk full" }) } : response(payload('A')); } });
  const { c, node, route } = controller(env);
  await c.fetchNode(route());
  assert.equal(calls, 1);
  assert.match(node.innerHTML, /Heatmap could not be loaded/);
  assert.doesNotMatch(node.innerHTML, /SQLite history is not enabled|No GPU history/);
  failing = false;
  c.handleClick({ dataset: { analyticsAction: 'node-refresh' } });
  await tick();
  assert.equal(calls, 2);
  assert.match(node.innerHTML, /heatmap-scroll/);
  assert.doesNotMatch(node.innerHTML, /could not be loaded/);
});

test('failed refresh preserves previously loaded heatmap and remains retryable', async () => {
  let failing = false, calls = 0;
  const env = environment({ fetch: async () => { calls++; return failing ? { ok: false, status: 500, json: async () => ({ error: "disk full" }) } : response(payload('A')); } });
  const { c, node, route } = controller(env);
  await c.fetchNode(route());
  failing = true;
  await c.fetchNode(route(), true);
  assert.match(node.innerHTML, /heatmap-scroll/);
  assert.match(node.innerHTML, /could not be loaded/);
  failing = false;
  await c.fetchNode(route());
  assert.equal(calls, 3);
  assert.doesNotMatch(node.innerHTML, /could not be loaded/);
});

test('old node responses cannot replace the current node after navigation', async () => {
  const pending = [];
  const env = environment({ fetch: (url) => new Promise(resolve => pending.push({ url, resolve })) });
  const { c, node, route, setRoute } = controller(env);
  const a = c.fetchNode(route());
  setRoute({ kind: 'node', nodeId: 'B' });
  const b = c.fetchNode(route());
  pending[1].resolve(response(payload('B')));
  await b;
  pending[0].resolve(response(payload('A')));
  await a;
  assert.match(node.innerHTML, /B trends/);
  assert.doesNotMatch(node.innerHTML, /A trends/);
});

test('timeout releases loading and manual refresh cancels a hung request', async () => {
  const signals = [];
  let hung = true;
  const env = environment({ fetch: (_url, options) => {
    signals.push(options.signal);
    if (!hung) return Promise.resolve(response(payload('A')));
    return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted'))));
  } });
  const { c, node, route } = controller(env);
  const first = c.fetchNode(route());
  const replacement = c.fetchNode(route(), true);
  assert.equal(signals[0].aborted, true);
  await first;
  for (const timer of env.timers.values()) if (timer.ms === 15_000) timer.fn();
  await replacement;
  assert.match(node.innerHTML, /Heatmap could not be loaded/);
  hung = false;
  await c.fetchNode(route(), true);
  assert.match(node.innerHTML, /heatmap-scroll/);
});

test('visibility recovery retires old sockets and obtains fresh data', () => {
  const env = environment();
  const messages = []; let recoveries = 0;
  const connection = env.load('live-connection').connectLive({ message: data => messages.push(data), state: () => {}, recover: () => recoveries++, interval: () => 1 });
  env.sockets[0].emit('message', { data: 'first' });
  env.document.hidden = true;
  env.document.emit('visibilitychange');
  assert.equal(env.sockets[0].closed, true);
  env.document.hidden = false;
  env.document.emit('visibilitychange');
  assert.equal(env.sockets.length, 2);
  assert.equal(recoveries, 1);
  env.sockets[0].emit('message', { data: 'stale' });
  env.sockets[1].emit('message', { data: 'latest' });
  assert.deepEqual(messages, ['first', 'latest']);
  connection.dispose();
  assert.equal(env.timers.size, 0);
});

test('authorization close cooldown is not bypassed by the watchdog', () => {
  const env = environment();
  const connection = env.load('live-connection').connectLive({ message: () => {}, state: () => {}, recover: () => {}, interval: () => 1 });
  env.sockets[0].emit('close', { code: 4403 });
  for (const timer of env.timers.values()) if (timer.ms === 3000) timer.fn();
  assert.equal(env.sockets.length, 1);
  assert.ok([...env.timers.values()].some(timer => timer.ms === 30_000));
  connection.dispose();
});

test('manual recovery is distinct from automatic wake recovery', () => {
  const env = environment();
  const modes = [];
  const connection = env.load('live-connection').connectLive({ message: () => {}, state: () => {}, recover: manual => modes.push(manual), interval: () => 1 });
  connection.recover();
  env.window.emit('online');
  assert.deepEqual(modes, [true, false]);
  connection.dispose();
});

test('overview errors remain retryable without claiming history is disabled', async () => {
  let failing = true;
  const env = environment({ fetch: async () => failing ? { ok: false, status: 500, json: async () => null } : response({ enabled: true }) });
  const overview = element();
  const c = env.load('analytics').createAnalyticsController({ overviewElement: overview, nodeElement: element(), jobElement: element(), currentRoute: () => ({ kind: 'overview' }), renderIcons: () => {} });
  await c.fetchOverview();
  assert.match(overview.innerHTML, /History could not be loaded/);
  assert.doesNotMatch(overview.innerHTML, /SQLite history is not enabled/);
  failing = false;
  await c.fetchOverview();
  assert.doesNotMatch(overview.innerHTML, /History could not be loaded/);
});

test('non-24h history refresh retries the separate heatmap request', async () => {
  const calls = [];
  let failHeatmap = false;
  const env = environment({ fetch: async url => {
    calls.push(url);
    return failHeatmap && url.endsWith('range=24h')
      ? { ok: false, status: 500, json: async () => null }
      : response(payload('A'));
  } });
  const { c, node, route } = controller(env);
  await c.fetchNode(route());
  failHeatmap = true;
  c.handleClick({ dataset: { analyticsAction: 'node-range', range: '7d' } });
  await tick();
  assert.equal(calls.length, 3);
  assert.match(node.innerHTML, /Heatmap could not be loaded/);
  failHeatmap = false;
  c.handleClick({ dataset: { analyticsAction: 'node-refresh' } });
  await tick();
  assert.equal(calls.length, 5);
  assert.match(node.innerHTML, /heatmap-scroll/);
  assert.doesNotMatch(node.innerHTML, /Heatmap could not be loaded/);
});
