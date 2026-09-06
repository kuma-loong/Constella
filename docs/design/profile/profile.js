// Isolated, fixture-backed design preview. No production APIs or account mutations.
(() => {
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const D = window.ProfileData;
  const { jobs, dateLabel } = D;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let period = 7, filter = 'all', selectedDate = '', selectedModel = '', scenario = 'ready';
  let selectedJob = jobs[0], detailTab = 'overview', showAll = false;
  const meaningfulData = () => scenario === 'ready' || scenario === 'partial';
  const icons = () => window.lucide?.createIcons({ attrs: { 'stroke-width': 1.7 } });
  const setPressed = (selector, attribute, value) => $$(selector).forEach((button) => button.setAttribute('aria-pressed', String(button.dataset[attribute] === String(value))));
  const partial = () => scenario === 'partial';
  const summarize = (days = D.dates(period), model = '') => D.summary(days, model, partial());
  function info(title, content) {
    $('#info-title').textContent = title;
    $('#info-body').innerHTML = content;
    $('#info-dialog').showModal();
    icons();
  }
  function settings() {
    info('Account settings', '<p>Manage your name and connected lab accounts.</p><dl class="metadata"><div><dt>Display name</dt><dd>Gu Quansheng</dd></div><div><dt>Connected accounts</dt><dd>' + (scenario === 'unbound' ? 'No connected accounts' : '3 lab nodes') + '</dd></div></dl><p class="preview-hint">Account editing is unavailable in this preview.</p>');
  }
  const timeline = new window.ActivityTimeline({
    onSelect: (date) => { selectedDate = date; render(); },
    onJob: (job) => openJob(job),
    onInspect: (ids, label) => {
      $$('.job-row').forEach((row) => row.classList.toggle('is-related', ids.includes(row.dataset.id)));
      $('.activity-panel .section-description').textContent = label;
    },
  });
  function render() {
    if (timeline.mobile.matches && !selectedDate) selectedDate = timeline.mobileDate;
    const days = D.dates(period);
    const total = summarize();
    $('#period-label').textContent = `${dateLabel(days[0])} – Sep 6, 2026 · UTC+8`;
    $('#gpu-hours').innerHTML = `${total.hours.toFixed(1)} <small>GPU·h</small>`;
    $('#active-hours').innerHTML = `${total.active.toFixed(1)} <small>h</small>`;
    $('#active-days').innerHTML = `${total.activeDays} <small>/ ${period}</small>`;
    $('#active-note').textContent = period === 7 ? 'This week' : 'In this period';
    $('#activity-title').textContent = period === 7 ? 'Weekly activity' : 'Activity · Last 30 days';
    $('#activity-filter').hidden = !selectedModel;
    $('#activity-filter-label').textContent = `${selectedModel} · ${summarize(days, selectedModel).hours.toFixed(1)} GPU·h in this period`;
    timeline.render({ days, model: selectedModel, selected: selectedDate, partial: partial() });
    renderModels();
    renderSelection();
    renderJobs();
    applyScenario();
    icons();
  }
  function renderModels() {
    const total = summarize().hours;
    $('#model-list').innerHTML = D.models.map((model) => {
      const hours = summarize(D.dates(period), model).hours;
      return `<button class="gpu-model" data-model="${model}" aria-pressed="${selectedModel === model}" aria-label="Filter by ${model}"><strong>${model}</strong><span class="model-hours">${hours.toFixed(1)} h</span><span class="model-share">${Math.round(hours / total * 100)}% of GPU hours</span>${selectedModel === model ? '<i data-lucide="check"></i>' : ''}</button>`;
    }).join('');
  }
  function renderSelection() {
    const dates = selectedDate ? [selectedDate] : D.dates(period);
    const total = summarize(dates, selectedModel);
    const count = new Set(dates.flatMap((date) => D.dayJobs(date, selectedModel).map((job) => job.id))).size;
    $('#selection-summary p').textContent = selectedDate ? `${D.weekday(selectedDate)}, ${dateLabel(selectedDate)}` : period === 7 ? 'Across this week' : 'Across this period';
    $('#selection-summary strong').textContent = `${total.hours.toFixed(1)} GPU·h`;
    $('#selection-summary>span').textContent = `${count} jobs · ${D.duration(total.active)} active`;
    $('#date-filter').hidden = !selectedDate && !selectedModel;
    $('#date-filter span').textContent = [selectedDate ? dateLabel(selectedDate) : '', selectedModel, 'Running jobs included'].filter(Boolean).join(' · ');
  }
  function renderJobs() {
    const query = $('#search').value.trim().toLowerCase();
    const periodStart = D.midnight(D.dates(period)[0]);
    const visible = meaningfulData() ? jobs.filter((job) => {
      const dateMatch = selectedDate ? job.startMs < D.midnight(selectedDate) + D.DAY && job.endMs > D.midnight(selectedDate) : job.endMs > periodStart;
      return (filter === 'all' || job.state === filter) && (job.state === 'running' || (dateMatch && (!selectedModel || job.model === selectedModel))) && `${job.name} ${job.node} ${job.model} ${job.pid}`.toLowerCase().includes(query);
    }) : [];
    const limited = showAll ? visible : visible.slice(0, 8);
    $('#running-count').textContent = meaningfulData() ? '2 running' : 'No running jobs';
    $('#running-shortcut').hidden = !meaningfulData();
    $('#full-jobs').innerHTML = `${showAll ? 'Show recent' : 'All jobs'} <i data-lucide="${showAll ? 'arrow-up' : 'arrow-down'}"></i>`;
    $('#job-list').innerHTML = limited.length ? limited.map((job) => `<article class="job-row" data-id="${job.id}"><div><div class="job-title">${job.name}</div><div class="job-sub ${job.state}">${job.state === 'running' ? '<span class="dot"></span>Running' : 'Ended'}<span>· PID ${job.pid}</span></div></div><div class="job-location">${job.node}<div class="job-sub">${job.gpu}</div></div><div class="job-time">${job.duration}</div><div class="job-seen">${job.seen}</div><button class="job-open" data-job="${job.id}" aria-label="View ${job.name}"><i data-lucide="arrow-up-right"></i></button></article>`).join('') + `<p class="job-count">${showAll ? visible.length : Math.min(8, visible.length)} of ${visible.length} matching jobs</p>` : `<div class="empty-jobs">${meaningfulData() ? 'No matching jobs. Try another search or clear the filters.' : scenario === 'loading' ? 'Loading jobs…' : 'No jobs to show yet.'}</div>`;
    icons();
  }
  function applyScenario() {
    const messages = {
      empty: ['No GPU activity in this period', 'Your jobs will appear here when you start using a connected node.'],
      unbound: ['Connect your first account', 'Connect your account on a lab node to see your jobs and GPU activity here.'],
      partial: ['Some activity is missing', 'Sep 4, 13:00–17:00 was not recorded. Totals exclude this time.'],
      loading: ['Loading your activity', 'Gathering your recorded jobs and GPU hours.'],
      error: ['Your activity could not be loaded', 'Please try again in a moment.'],
    };
    $('#dashboard').hidden = !meaningfulData();
    $('#notice').hidden = scenario === 'ready';
    $('#binding-count').textContent = scenario === 'unbound' ? 'No connected accounts' : '3 connected accounts';
    $('#coverage').textContent = partial() ? 'Partial history · Totals exclude missing activity' : 'Sample activity · Updated Sep 6 at 16:42';
    $('.missing-legend').hidden = !partial();
    if (messages[scenario]) {
      const [title, text] = messages[scenario];
      $('#notice').innerHTML = `<strong>${title}</strong><p>${text}</p>${scenario === 'unbound' ? '<button id="connect-account">Connect an account</button>' : scenario === 'error' ? '<button id="retry">Try again</button>' : scenario === 'loading' ? '<div class="loading-skeleton" aria-hidden="true"><span></span><span></span><span></span></div>' : ''}`;
    }
  }
  function openJob(job) {
    selectedJob = job;
    $$('.job-row').forEach((row) => row.classList.toggle('is-selected', row.dataset.id === job.id));
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
      $('#detail-body').innerHTML = `<label class="metric-picker">Metric<select id="detail-metric">${options.map((label) => `<option>${label}</option>`).join('')}</select></label><p class="chart-description">Sample GPU activity during this job<br>${job.node} / GPU 0 · From ${job.start}</p><div id="curve"></div><p class="chart-description">These readings cover the whole GPU, including any other jobs sharing it.</p>`;
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
  $('#running-shortcut').onclick = () => {
    filter = 'running'; setPressed('[data-filter]', 'filter', filter); renderJobs();
    $('#jobs-title').scrollIntoView({ behavior: reduced.matches ? 'instant' : 'smooth', block: 'start' });
    $('[data-filter="running"]').focus({ preventScroll: true });
  };
  $('#definition').onclick = () => info('About GPU hours', '<p>Using 4 GPUs for 2 hours adds up to 8 GPU hours.</p><p>Time with active jobs counts those same 2 hours once, even when several jobs run together. Jobs sharing the same GPU do not count it twice.</p><p>Missing activity is excluded from the totals.</p>');
  $('#full-jobs').onclick = () => { showAll = !showAll; renderJobs(); };
  $('#analyze').onclick = () => { $('#detail-status').textContent = 'Full job analysis is unavailable in this preview.'; };
  $('#copy-command').onclick = async () => {
    try { await navigator.clipboard.writeText(selectedJob.command); $('#detail-status').textContent = 'Sample command copied'; }
    catch { $('#detail-status').textContent = 'Copy is unavailable. Select the command in Overview to copy it manually.'; }
  };
  $$('[data-detail]').forEach((button) => { button.onclick = () => { detailTab = button.dataset.detail; renderDetail(); }; });
  $$('[data-period]').forEach((button) => { button.onclick = () => {
    if (period === Number(button.dataset.period)) return;
    period = Number(button.dataset.period); selectedDate = ''; showAll = false;
    setPressed('[data-period]', 'period', period); render();
    if (!reduced.matches) $('#activity-days').animate([{ opacity: .65, transform: 'translateX(8px)' }, { opacity: 1, transform: 'translateX(0)' }], { duration: 200, easing: 'ease-out' });
  }; });
  $$('[data-filter]').forEach((button) => { button.onclick = () => { filter = button.dataset.filter; setPressed('[data-filter]', 'filter', filter); renderJobs(); }; });
  $('#search').oninput = renderJobs;
  $('#clear-date').onclick = () => { selectedDate = ''; selectedModel = ''; timeline.mobileDate = '2026-09-06'; render(); $('#search').focus({ preventScroll: true }); };
  $('#clear-model').onclick = () => { const previous = selectedModel; selectedModel = ''; render(); $(`[data-model="${previous}"]`)?.focus(); };
  $('#model-list').onclick = (event) => {
    const button = event.target.closest('[data-model]');
    if (!button) return;
    const model = button.dataset.model;
    selectedModel = selectedModel === model ? '' : model;
    render();
    $(`[data-model="${model}"]`).focus({ preventScroll: true });
    if (!reduced.matches) $('#activity-days').animate([{ opacity: .55 }, { opacity: 1 }], { duration: 180 });
  };
  $('#job-list').onclick = (event) => { const button = event.target.closest('[data-job]'); if (button) openJob(jobs.find((job) => job.id === button.dataset.job)); };
  $('#notice').onclick = (event) => {
    if (event.target.closest('#connect-account')) settings();
    if (event.target.closest('#retry')) { scenario = 'ready'; $('#scenario').value = 'ready'; render(); $('#scenario').focus(); }
  };
  $('#scenario').onchange = () => { scenario = $('#scenario').value; selectedDate = ''; selectedModel = ''; render(); };
  function themeIcon() { $('#theme').innerHTML = `<i data-lucide="${document.documentElement.dataset.theme === 'dark' ? 'sun' : 'moon'}"></i>`; icons(); }
  $('#theme').onclick = () => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; themeIcon(); };
  $$('.close-dialog').forEach((button) => { button.onclick = () => button.closest('dialog').close(); });
  $$('dialog').forEach((dialog) => { dialog.addEventListener('click', (event) => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  }); });
  $('#detail-dialog').addEventListener('close', () => $$('.job-row').forEach((row) => row.classList.remove('is-selected')));
  document.documentElement.dataset.theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  render(); themeIcon();
})();
