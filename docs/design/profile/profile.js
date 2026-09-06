// Design-only fixture data. No API calls, real identity, or production state.
(() => {
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const fixtures = {
    7: { days: [12.8, 19.6, 0, 23.4, 15.8, 30.2, 24.6], active: 46.2, delta: 18.6, activeDays: 6, favorite: 72, peak: [20, 23] },
    30: { days: [8, 12, 4, 0, 14, 19, 6, 8, 0, 12, 18, 11, 6, 9, 13, 4, 0, 11, 17, 9, 10, 15, 14, 12.8, 19.6, 0, 23.4, 15.8, 30.2, 24.6], active: 157.8, delta: 42.6, activeDays: 26, favorite: 68, peak: [19, 22] },
  };
  const jobs = [
    { id: 'demo-train-0906', name: 'train_llama.py', node: 'gpu-server-03', gpu: '4 × H100', state: 'running', duration: '04h 12m', seen: 'Just now', date: '2026-09-06', start: '09-06 12:30', pid: 28416, command: 'torchrun --nproc_per_node=4 train_llama.py --config configs/finetune.yaml', memory: '46.8 GiB', performance: true },
    { id: 'demo-eval-0906', name: 'evaluate.py', node: 'gpu-server-01', gpu: '1 × RTX PRO 6000', state: 'running', duration: '00h 38m', seen: 'Just now', date: '2026-09-06', start: '09-06 16:04', pid: 31902, command: 'python evaluate.py --checkpoint outputs/latest', memory: '18.2 GiB', performance: false },
    { id: 'demo-pretrain-0905', name: 'pretrain.py', node: 'gpu-server-03', gpu: '4 × H100', state: 'ended', duration: '06h 24m', seen: 'Yesterday, 23:48', date: '2026-09-05', start: '09-05 17:24', pid: 27104, command: 'torchrun --nproc_per_node=4 pretrain.py --config configs/base.yaml', memory: '52.3 GiB', performance: true },
    { id: 'demo-embed-0904', name: 'generate_embeddings.py', node: 'gpu-server-02', gpu: '2 × A100', state: 'ended', duration: '02h 18m', seen: '09-04 22:16', date: '2026-09-04', start: '09-04 19:58', pid: 18392, command: 'python generate_embeddings.py --batch-size 128', memory: '23.1 GiB', performance: true },
    { id: 'demo-bench-0903', name: 'benchmark.py', node: 'gpu-server-01', gpu: '1 × RTX PRO 6000', state: 'ended', duration: '00h 56m', seen: '09-03 21:38', date: '2026-09-03', start: '09-03 20:42', pid: 16280, command: 'python benchmark.py --suite inference', memory: '12.4 GiB', performance: false },
    { id: 'demo-aug-0822', name: 'train_encoder.py', node: 'gpu-server-02', gpu: '2 × A100', state: 'ended', duration: '03h 41m', seen: '08-22 22:07', date: '2026-08-22', start: '08-22 18:26', pid: 12874, command: 'python train_encoder.py --epochs 20', memory: '31.4 GiB', performance: true },
  ];
  let period = 7;
  let filter = 'all';
  let selectedDate = '';
  let scenario = 'ready';
  let selectedJob = jobs[0];
  let detailTab = 'overview';
  let numberFrame = 0;
  let displayedTotal = 126.4;
  const total = () => fixtures[period].days.reduce((a, b) => a + b, 0);
  const icons = () => window.lucide?.createIcons({ attrs: { 'stroke-width': 1.7 } });
  const dateAt = (index) => new Date(Date.UTC(2026, 8, 6 - period + 1 + index)).toISOString().slice(0, 10);
  const dateLabel = (date) => new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));
  const meaningfulData = () => scenario === 'ready' || scenario === 'partial';
  const setPressed = (selector, attribute, value) => $$(selector).forEach((button) => button.setAttribute('aria-pressed', String(button.dataset[attribute] === String(value))));
  function info(title, content) {
    $('#info-title').textContent = title;
    $('#info-body').innerHTML = content;
    $('#info-dialog').showModal();
    icons();
  }
  function settings() {
    info('Account settings', '<p>Manage your name and connected lab accounts.</p><dl class="metadata"><div><dt>Display name</dt><dd>Gu Quansheng</dd></div><div><dt>Connected accounts</dt><dd>3 lab nodes</dd></div></dl><p class="preview-hint">Account editing is unavailable in this preview.</p>');
  }
  function animateNumber(target) {
    cancelAnimationFrame(numberFrame);
    const startValue = displayedTotal;
    const startTime = performance.now();
    function frame(now) {
      const progress = reduced.matches ? 1 : Math.min(1, (now - startTime) / 550);
      displayedTotal = startValue + (target - startValue) * (1 - Math.pow(1 - progress, 3));
      $('#gpu-hours').textContent = displayedTotal.toFixed(1);
      if (progress < 1) numberFrame = requestAnimationFrame(frame);
    }
    numberFrame = requestAnimationFrame(frame);
  }
  function hourlyActivity(data) {
    return Array.from({ length: 25 }, (_, hour) => {
      const distance = Math.min(Math.abs(hour - data.peak[0] - 1), 24 - Math.abs(hour - data.peak[0] - 1));
      return .12 + .85 * Math.exp(-(distance * distance) / 9) + .08 * (1 + Math.sin(hour * 1.7));
    });
  }
  function renderStats() {
    const data = fixtures[period];
    animateNumber(total());
    $('#comparison').textContent = `+${data.delta.toFixed(1)} GPU·h from the previous period`;
    $('#active-hours').innerHTML = `${data.active.toFixed(1)} <small>h</small>`;
    $('#active-days').innerHTML = `${data.activeDays} <small>/ ${period} days</small>`;
    $('#favorite-share').textContent = `${data.favorite}% of GPU hours`;
    $('#peak-time').innerHTML = `${data.peak[0]}:00<span>—</span>${data.peak[1]}:00`;
    $('#period-label').textContent = `${dateLabel(dateAt(0))} — Sep 6, 2026 · UTC+8`;
    dailyTrace.setData(data.days, {
      axes: true,
      labels: [0, Math.floor((period - 1) / 2), period - 1].map((index) => ({ index, text: dateLabel(dateAt(index)) })),
      selectedIndex: selectedDate ? Array.from({ length: period }, (_, index) => dateAt(index)).indexOf(selectedDate) : -1,
      description: 'Daily GPU hours. Each point represents one day.',
    });
    const hours = hourlyActivity(data);
    rhythmTrace.setData(hours, {
      highlight: data.peak,
      hourly: true,
      description: `Relative GPU activity by hour, busiest from ${data.peak[0]}:00 to ${data.peak[1]}:00.`,
    });
    const modelData = [['H100', data.favorite], ['A100', 100 - data.favorite - 10], ['RTX PRO 6000', 10]];
    $('#model-list').innerHTML = modelData.map(([name, share]) => `<div class="model"><span>${name}</span><div class="model-track" role="img" aria-label="${name}: ${share}%"><span style="--value:${share / 100}"></span></div><span class="model-value">${(total() * share / 100).toFixed(1)} h · ${share}%</span></div>`).join('');
    renderDateSelection();
  }
  function readout(index) {
    $('#bar-readout').textContent = `${dateLabel(dateAt(index))} · ${fixtures[period].days[index].toFixed(1)} GPU·h`;
  }
  function renderDateSelection() {
    const index = selectedDate ? Array.from({ length: period }, (_, i) => dateAt(i)).indexOf(selectedDate) : -1;
    dailyTrace.options.selectedIndex = index;
    dailyTrace.setActive(index);
    readout(index >= 0 ? index : fixtures[period].days.indexOf(Math.max(...fixtures[period].days)));
    $('#date-filter').hidden = !selectedDate;
    $('#date-filter span').textContent = `Jobs on ${dateLabel(selectedDate || dateAt(0))} · Running jobs included`;
  }
  function renderJobs() {
    const query = $('#search').value.trim().toLowerCase();
    const visible = meaningfulData() ? jobs.filter((job) => (filter === 'all' || filter === job.state) && (job.state === 'running' || (job.date >= dateAt(0) && (!selectedDate || job.date === selectedDate))) && `${job.name} ${job.node} ${job.pid}`.toLowerCase().includes(query)) : [];
    $('#running-count').textContent = meaningfulData() ? '2 running' : 'No running jobs';
    $('#running-shortcut').hidden = !meaningfulData();
    $('#job-list').innerHTML = visible.length ? visible.map((job) => `<article class="job-row"><div><div class="job-title">${job.name}</div><div class="job-sub ${job.state}">${job.state === 'running' ? '<span class="dot"></span>Running' : 'Ended'}<span>· PID ${job.pid}</span></div></div><div class="job-location">${job.node}<div class="job-sub">${job.gpu}</div></div><div class="job-time">${job.duration}</div><div class="job-seen">${job.seen}</div><button class="job-open" data-job="${job.id}" aria-label="View ${job.name}"><i data-lucide="arrow-up-right"></i></button></article>`).join('') : `<div class="empty-jobs">${meaningfulData() ? 'No matching jobs. Try another search or clear the date.' : 'No jobs to show yet.'}</div>`;
    $$('[data-job]').forEach((button) => { button.onclick = () => openJob(jobs.find((job) => job.id === button.dataset.job)); });
    icons();
  }
  function applyScenario() {
    const messages = {
      empty: ['No GPU activity in this period', 'Your jobs will appear here when you start using a connected node. Try a different period to see earlier activity.'],
      unbound: ['Connect your first account', 'Connect your account on a lab node to see your jobs and GPU activity here.'],
      partial: ['Some activity is missing', 'These totals include the history available for your accounts. Some activity could not be recorded.'],
      error: ['Your activity could not be loaded', 'Please try again in a moment.'],
    };
    $('#dashboard').hidden = !meaningfulData();
    $('#hardware').hidden = !meaningfulData();
    $('#notice').hidden = scenario === 'ready';
    $('#binding-count').textContent = scenario === 'unbound' ? 'No connected accounts' : '3 connected accounts';
    $('#coverage').textContent = scenario === 'partial' ? 'Partial history · Totals may be incomplete' : 'Based on your recorded GPU activity';
    if (messages[scenario]) {
      const [title, text] = messages[scenario];
      $('#notice').innerHTML = `<strong>${title}</strong><p>${text}</p>${scenario === 'unbound' ? '<button id="connect-account">Connect an account</button>' : scenario === 'error' ? '<button id="retry">Try again</button>' : ''}`;
      if ($('#connect-account')) $('#connect-account').onclick = settings;
      if ($('#retry')) $('#retry').onclick = () => { scenario = 'ready'; $('#scenario').value = 'ready'; applyScenario(); };
    }
    renderJobs();
  }
  function openJob(job) {
    selectedJob = job;
    detailTab = 'overview';
    $('#detail-title').textContent = job.name;
    $('#detail-eyebrow').textContent = job.state === 'running' ? 'RUNNING JOB' : 'ENDED JOB';
    $('#detail-status').textContent = '';
    renderDetail();
    $('#detail-dialog').showModal();
  }
  function meta(label, value) { return `<div><dt>${label}</dt><dd>${value}</dd></div>`; }
  function renderDetail() {
    const job = selectedJob;
    setPressed('[data-detail]', 'detail', detailTab);
    if (detailTab === 'overview') {
      $('#detail-body').innerHTML = `<dl class="metadata">${meta('Status', job.state === 'running' ? 'Running' : 'Ended · Outcome unavailable')}${meta('Node', job.node)}${meta('PID', job.pid)}${meta('Started · UTC+8', job.start)}${meta('Duration', job.duration)}${meta('GPU', job.gpu)}</dl><h3>Command</h3><code class="command">${job.command}</code><h3>GPU memory</h3><p>${job.memory} <span class="job-sub">${job.state === 'running' ? 'Current' : 'Last recorded'}</span></p>`;
    } else if (detailTab === 'performance' && !job.performance) {
      $('#detail-body').innerHTML = '<div class="notice"><strong>Performance metrics unavailable</strong><p>This GPU does not provide these metrics. You can still explore utilization and memory in the GPU tab.</p></div>';
    } else {
      const options = detailTab === 'gpu' ? ['GPU utilization (%)', 'GPU memory (GiB)'] : ['SM Active (%)', 'DRAM Active (%)'];
      $('#detail-body').innerHTML = `<label class="metric-picker">Metric<select id="detail-metric">${options.map((label) => `<option>${label}</option>`).join('')}</select></label><p class="chart-description">GPU activity during this job<br>${job.node} / GPU 0 · From ${job.start}</p><div id="curve"></div><p class="chart-description">These readings cover the whole GPU, including any other jobs sharing it.</p>`;
      renderCurve();
      $('#detail-metric').onchange = renderCurve;
    }
    icons();
  }
  function renderCurve() {
    const label = $('#detail-metric').value;
    const memory = label.includes('GiB');
    const ceiling = memory ? 80 : 100;
    const width = Math.max(230, $('#curve').clientWidth);
    const height = 190;
    const x0 = 48, x1 = width - 12, y0 = 12, y1 = 155;
    const values = Array.from({ length: 40 }, (_, index) => Math.max(0, Math.min(ceiling, (memory ? 40 : label.includes('DRAM') ? 33 : 70) + 9 * Math.sin(index * .67) + 5 * Math.cos(index * 1.9))));
    const path = values.map((value, index) => `${index ? 'L' : 'M'}${x0 + index / 39 * (x1 - x0)},${y1 - value / ceiling * (y1 - y0)}`).join(' ');
    $('#curve').innerHTML = `<svg class="mini-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${label} sample history"><title>${label} · GPU activity, sample data</title>${[0, .5, 1].map((ratio) => `<line x1="${x0}" x2="${x1}" y1="${y1 - ratio * (y1 - y0)}" y2="${y1 - ratio * (y1 - y0)}" stroke="var(--border)"/><text x="38" y="${y1 - ratio * (y1 - y0) + 4}" text-anchor="end">${ceiling * ratio}</text>`).join('')}<path d="${path}" fill="none" stroke="var(--telemetry)" stroke-width="2"/><text x="${x0}" y="180">Start</text><text x="${x1}" y="180" text-anchor="end">${selectedJob.duration} later</text></svg>`;
  }
  new ResizeObserver(() => { if ($('#detail-dialog').open && $('#curve')) renderCurve(); }).observe($('#detail-body'));
  $('#settings').onclick = settings;
  $('#running-shortcut').onclick = () => { filter = 'running'; setPressed('[data-filter]', 'filter', filter); renderJobs(); $('#jobs-title').scrollIntoView({ behavior: reduced.matches ? 'instant' : 'smooth', block: 'start' }); document.querySelector('[data-filter="running"]').focus({ preventScroll: true }); };
  $('#definition').onclick = () => info('About GPU hours', '<p>Using 4 GPUs for 2 hours adds up to 8 GPU hours.</p><p>Time with active jobs counts those same 2 hours once, even when several jobs run together.</p><p>GPU hours describe usage time, not how efficiently a job runs. Totals may be incomplete when activity is missing.</p>');
  $('#full-jobs').onclick = () => info('All your jobs', '<p>Search your job history and explore each run in detail.</p><p class="preview-hint">The full Jobs page is unavailable in this preview.</p>');
  $('#analyze').onclick = () => { $('#detail-status').textContent = 'Full job analysis is unavailable in this preview.'; };
  $('#copy-command').onclick = async () => { try { await navigator.clipboard.writeText(selectedJob.command); $('#detail-status').textContent = 'Sample command copied'; } catch { $('#detail-status').textContent = 'Copy is unavailable. Select the command in Overview to copy it manually.'; } };
  $$('[data-detail]').forEach((button) => { button.onclick = () => { detailTab = button.dataset.detail; renderDetail(); }; });
  $$('[data-period]').forEach((button) => { button.onclick = () => { period = Number(button.dataset.period); selectedDate = ''; setPressed('[data-period]', 'period', period); renderStats(); renderJobs(); }; });
  $$('[data-filter]').forEach((button) => { button.onclick = () => { filter = button.dataset.filter; setPressed('[data-filter]', 'filter', filter); renderJobs(); }; });
  $('#search').oninput = renderJobs;
  $('#clear-date').onclick = () => { selectedDate = ''; renderDateSelection(); renderJobs(); };
  $('#scenario').onchange = () => { scenario = $('#scenario').value; selectedDate = ''; renderDateSelection(); applyScenario(); };
  $('#theme').onclick = () => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; $('#theme').innerHTML = `<i data-lucide="${document.documentElement.dataset.theme === 'dark' ? 'sun' : 'moon'}"></i>`; icons(); };
  $$('.close-dialog').forEach((button) => { button.onclick = () => button.closest('dialog').close(); });
  $$('dialog').forEach((dialog) => { dialog.addEventListener('click', (event) => { if (event.target !== dialog) return; const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); }); });
  document.documentElement.dataset.theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  const dailyTrace = new window.ProfileTrace($('#daily-chart'), {
    interactive: true,
    onInspect: readout,
    valueLabel: (index) => `${dateLabel(dateAt(index))}: ${fixtures[period].days[index].toFixed(1)} GPU hours`,
    onSelect: (index) => { const date = dateAt(index); selectedDate = selectedDate === date ? '' : date; renderDateSelection(); renderJobs(); },
  });
  const rhythmTrace = new window.ProfileTrace($('#hour-chart'));
  renderStats(); applyScenario(); icons();
})();
