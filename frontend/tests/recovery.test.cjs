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
      dispatchEvent(event) { this.emit(event.type, event); },
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
  const globals = { console, URLSearchParams, AbortController, Headers, Event, Date, Intl, window, document, navigator: { onLine: true }, location: { protocol: 'http:', host: 'test' }, WebSocket, ...overrides };
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

test('visibility recovery retires old sockets and obtains fresh data', async () => {
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
  await tick();
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

test('manual recovery is distinct from automatic wake recovery', async () => {
  let now = 0;
  const env = environment({ Date: { now: () => now } });
  const modes = [];
  const connection = env.load('live-connection').connectLive({ message: () => {}, state: () => {}, recover: manual => modes.push(manual), interval: () => 1 });
  connection.recover();
  await tick();
  now = 2000;
  env.window.emit('online');
  await tick();
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

test('wake event burst starts one connection and one slow HTTP recovery', async () => {
  let now = 0, probes = 0, refreshes = 0, release;
  const env = environment({ Date: { now: () => now } });
  const connection = env.load('live-connection').connectLive({
    message: () => {}, state: () => {}, interval: () => 1,
    refresh: () => refreshes++, recover: () => { probes++; return new Promise(resolve => { release = resolve; }); },
  });
  now = 60_000;
  env.document.emit('visibilitychange');
  env.window.emit('focus');
  env.window.emit('pageshow');
  env.window.emit('online');
  await tick();
  assert.equal(env.sockets.length, 2);
  assert.equal(probes, 1);
  assert.equal(refreshes, 1);
  release(true);
  await tick();
  assert.equal(env.sockets.length, 2);
  connection.dispose();
});

test('slow handshake survives telemetry watchdog and manual refresh', async () => {
  let now = 0;
  const env = environment({ Date: { now: () => now } });
  const connection = env.load('live-connection').connectLive({ message: () => {}, state: () => {}, interval: () => 1, recover: () => true });
  const check = [...env.timers.values()].find(timer => timer.ms === 3000).fn;
  for (now = 3000; now <= 24_000; now += 3000) check();
  connection.recover();
  await tick();
  assert.equal(env.sockets.length, 1);
  assert.notEqual(env.sockets[0].closed, true);
  env.sockets[0].emit('message', { data: 'current' });
  connection.dispose();
});

test('opaque handshake failures probe HTTP auth and do not reload history', async () => {
  const env = environment();
  let probes = 0, refreshes = 0;
  const connection = env.load('live-connection').connectLive({
    message: () => {}, state: () => {}, interval: () => 1,
    recover: () => { probes++; return false; }, refresh: () => refreshes++,
  });
  env.sockets[0].emit('close', { code: 1006 });
  const [id, timer] = [...env.timers.entries()].find(([, t]) => t.ms === 1200);
  env.timers.delete(id);
  timer.fn();
  await tick();
  for (const t of env.timers.values()) t.fn();
  env.window.emit('online');
  assert.equal(probes, 1);
  assert.equal(refreshes, 0);
  assert.equal(env.sockets.length, 2);
  assert.equal(env.sockets[1].closed, true);
  connection.dispose();
});

test('HTTP failure cannot prevent a usable WebSocket from reconnecting', async () => {
  const env = environment();
  let failing = true;
  const connection = env.load('live-connection').connectLive({ message: () => {}, state: () => {}, interval: () => 1,
    recover: async () => { if (failing) throw new Error('offline'); return true; },
  });
  const fire = ms => { const [id, timer] = [...env.timers.entries()].find(([, t]) => t.ms === ms); env.timers.delete(id); timer.fn(); };
  env.sockets[0].emit('error');
  fire(1200);
  await tick();
  assert.equal(env.sockets.length, 2);
  env.sockets[1].emit('error');
  failing = false;
  fire(2400);
  await tick();
  assert.equal(env.sockets.length, 3);
  connection.dispose();
});

test('manual refresh replaces a pending probe and ignores its late authentication result', async () => {
  const env = environment();
  const pending = [], modes = [];
  const connection = env.load('live-connection').connectLive({
    message: () => {}, state: () => {}, interval: () => 1,
    recover: manual => { modes.push(manual); return new Promise(resolve => pending.push(resolve)); },
  });
  env.window.emit('online');
  await tick();
  connection.recover();
  await tick();
  assert.deepEqual(modes, [false, true]);
  pending[0](false);
  await tick();
  assert.notEqual(env.sockets.at(-1).closed, true);
  pending[1](true);
  await tick();
  connection.dispose();
});

test('a hanging HTTP probe does not disable the socket watchdog or retry', async () => {
  let now = 0, probes = 0;
  const env = environment({ Date: { now: () => now } });
  const connection = env.load('live-connection').connectLive({
    message: () => {}, state: () => {}, interval: () => 1,
    recover: () => { probes++; return new Promise(() => {}); },
  });
  connection.recover();
  await tick();
  const check = [...env.timers.values()].find(t => t.ms === 3000).fn;
  for (now = 3000; now <= 33_000; now += 3000) check();
  assert.equal(env.sockets[0].closed, true);
  const [id, timer] = [...env.timers.entries()].find(([, t]) => t.ms === 1200);
  env.timers.delete(id);
  timer.fn();
  await tick();
  assert.equal(env.sockets.length, 2);
  assert.equal(probes, 1);
  env.sockets[1].emit('message', { data: 'fresh' });
  assert.notEqual(env.sockets[1].closed, true);
  connection.dispose();
});

test('Access AJAX header preserves caller headers and expired session emits action without reload', async () => {
  let init, notified = 0;
  const env = environment({ fetch: async (_url, options) => { init = options; return new Response('', { status: 401 }); } });
  const requests = env.load('requests');
  env.window.addEventListener(requests.AUTHENTICATION_REQUIRED, () => notified++);
  await assert.rejects(requests.fetchJson('/api/lab/me', { headers: new Headers({ 'X-Test': 'retained' }) }), error => error.status === 401);
  assert.equal(init.headers.get('X-Requested-With'), 'XMLHttpRequest');
  assert.equal(init.headers.get('X-Test'), 'retained');
  assert.equal(init.credentials, 'same-origin');
  assert.equal(notified, 1);
  assert.equal(env.timers.size, 0);
});

test('disposing during an HTTP recovery cannot reopen a connection', async () => {
  const env = environment();
  let release;
  const connection = env.load('live-connection').connectLive({ message: () => {}, state: () => {}, interval: () => 1,
    recover: () => new Promise(resolve => { release = resolve; }),
  });
  connection.recover();
  await tick();
  connection.dispose();
  release(true);
  await tick();
  assert.equal(env.sockets.length, 1);
  assert.equal(env.sockets[0].closed, true);
  assert.equal(env.timers.size, 0);
});

test('invalid data reports an error without closing a working transport', async () => {
  const env = environment();
  const states = [];
  let probes = 0;
  const connection = env.load('live-connection').connectLive({
    message: data => JSON.parse(data), state: state => states.push(state),
    recover: () => { probes++; return true; }, interval: () => 1,
  });
  env.sockets[0].emit('message', { data: '{"metric": NaN}' });
  await tick();
  assert.equal(states.at(-1), 'error');
  assert.notEqual(env.sockets[0].closed, true);
  env.sockets[0].emit('message', { data: '{"metric": NaN}' });
  await tick();
  assert.equal(probes, 1);
  env.sockets[0].emit('message', { data: '{"metric": 42}' });
  assert.equal(states.at(-1), 'live');
  assert.equal(env.sockets.length, 1);
  connection.dispose();
});
