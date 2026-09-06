// Preview fixtures only. All dashboard totals are derived from these job intervals.
(() => {
  const DAY = 86400000;
  const HOUR = 3600000;
  const now = Date.parse('2026-09-06T16:42:00+08:00');
  const midnight = (date) => Date.parse(`${date}T00:00:00+08:00`);
  const dateKey = (time) => new Date(time + 8 * HOUR).toISOString().slice(0, 10);
  const dateLabel = (date) => new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));
  const weekday = (date) => new Intl.DateTimeFormat('en', { weekday: 'short', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));
  const clock = (hour) => `${String(Math.floor(hour)).padStart(2, '0')}:${String(Math.round((hour % 1) * 60)).padStart(2, '0')}`;
  const duration = (hours) => { const minutes = Math.round(hours * 60); return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`; };
  const models = ['H100', 'A100', 'RTX PRO 6000'];
  const jobs = [];
  function add(date, start, end, name, model, count, options = {}) {
    const index = jobs.length;
    const startMs = midnight(date) + start * HOUR;
    const endMs = Math.min(now, midnight(date) + end * HOUR);
    const node = `gpu-server-0${model === 'H100' ? 3 : model === 'A100' ? 2 : 1}`;
    jobs.push({
      id: `sample-job-${index}`, date, name, model, count, node,
      devices: Array.from({ length: count }, (_, i) => `${node}/${i + (options.offset || 0)}`),
      startMs, endMs, state: options.running ? 'running' : 'ended',
      gpu: `${count} × ${model}`, duration: duration((endMs - startMs) / HOUR),
      start: `${date.slice(5)} ${clock(start)}`, pid: 18392 + index * 317,
      seen: options.running ? 'Just now' : `${dateKey(endMs).slice(5)} ${clock((endMs - midnight(dateKey(endMs))) / HOUR)}`,
      command: `${count > 1 ? `torchrun --nproc_per_node=${count}` : 'python'} ${name} --config configs/${name.replace('.py', '')}.yaml`,
      memory: `${model === 'H100' ? '46.8' : model === 'A100' ? '28.4' : '18.2'} GiB`,
      performance: model !== 'RTX PRO 6000',
    });
  }
  // Older history uses repeatable schedules; no independent chart or metric fixtures.
  for (let i = 0; i < 23; i++) {
    if ([3, 9, 16].includes(i)) continue;
    const date = dateKey(midnight('2026-08-08') + i * DAY);
    add(date, 8.5 + i % 5, 12.25 + i % 5, ['train_encoder.py', 'evaluate.py', 'pretrain.py'][i % 3], models[i % 3], i % 3 === 2 ? 1 : 2);
    if (i % 4 === 0) add(date, 18.25, 22.5, 'generate_embeddings.py', 'A100', 2);
  }
  add('2026-08-31', 9.25, 12.5, 'prepare_embeddings.py', 'A100', 2);
  add('2026-08-31', 14, 17.75, 'train_encoder.py', 'H100', 2);
  add('2026-08-31', 20.5, 30.75, 'pretrain.py', 'H100', 4);
  add('2026-09-01', 10, 11.5, 'evaluate.py', 'RTX PRO 6000', 1);
  add('2026-09-01', 15.5, 20.75, 'finetune.py', 'H100', 4);
  add('2026-09-01', 18.25, 22.25, 'generate_embeddings.py', 'A100', 2);
  // Sep 2 is deliberately idle.
  add('2026-09-03', 8.5, 13, 'train_encoder.py', 'A100', 2);
  add('2026-09-03', 14.25, 22.5, 'finetune.py', 'H100', 4);
  add('2026-09-03', 20.7, 21.633333, 'benchmark.py', 'RTX PRO 6000', 1);
  add('2026-09-04', 9.75, 11.25, 'evaluate.py', 'RTX PRO 6000', 1);
  add('2026-09-04', 13.5, 18.5, 'train_encoder.py', 'H100', 2);
  add('2026-09-04', 19.966667, 22.266667, 'generate_embeddings.py', 'A100', 2);
  add('2026-09-05', 10.25, 13.5, 'benchmark.py', 'RTX PRO 6000', 1);
  add('2026-09-05', 17.4, 23.8, 'pretrain.py', 'H100', 4);
  add('2026-09-05', 19, 21.25, 'evaluate.py', 'RTX PRO 6000', 1);
  add('2026-09-06', 8.25, 10.5, 'prepare_embeddings.py', 'A100', 2);
  add('2026-09-06', 12.5, 16.7, 'train_llama.py', 'H100', 4, { running: true });
  add('2026-09-06', 16.066667, 16.7, 'evaluate.py', 'RTX PRO 6000', 1, { running: true });
  jobs.sort((a, b) => Number(b.state === 'running') - Number(a.state === 'running') || b.endMs - a.endMs);
  function dates(period) { return Array.from({ length: period }, (_, i) => dateKey(midnight('2026-09-06') - (period - 1 - i) * DAY)); }
  function dayJobs(date, model = '') {
    const start = midnight(date);
    return jobs.filter((job) => (!model || job.model === model) && job.startMs < start + DAY && job.endMs > start);
  }
  function segments(date, model = '', partial = false) {
    const start = midnight(date);
    const end = Math.min(start + DAY, now);
    const records = dayJobs(date, model);
    const gap = partial && date === '2026-09-04' ? [start + 13 * HOUR, start + 17 * HOUR] : null;
    const edges = [...new Set([start, end, ...records.flatMap((job) => [Math.max(start, job.startMs), Math.min(end, job.endMs)]), ...(gap || [])])].sort((a, b) => a - b);
    return edges.slice(0, -1).map((a, i) => {
      const b = edges[i + 1];
      const active = records.filter((job) => job.startMs < b && job.endMs > a);
      const missing = !!gap && a >= gap[0] && b <= gap[1];
      return { start: (a - start) / HOUR, end: (b - start) / HOUR, count: missing ? 0 : new Set(active.flatMap((job) => job.devices)).size, missing, jobs: active };
    });
  }
  function summary(days, model = '', partial = false) {
    let hours = 0, active = 0, activeDays = 0;
    days.forEach((date) => {
      let daily = 0;
      segments(date, model, partial).forEach((span) => {
        daily += (span.end - span.start) * span.count;
        if (span.count) active += span.end - span.start;
      });
      hours += daily;
      if (daily > 0) activeDays++;
    });
    return { hours, active, activeDays };
  }
  window.ProfileData = { jobs, models, now, DAY, HOUR, midnight, dateKey, dateLabel, weekday, clock, duration, dates, dayJobs, segments, summary };
})();
