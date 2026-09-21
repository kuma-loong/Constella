// Synthetic identities/base telemetry, plus an explicitly sanitized performance capture.
// Reproduction never connects to a database or production endpoint.
import {readFileSync} from 'node:fs';
import {gunzipSync} from 'node:zlib';
const captured = JSON.parse(gunzipSync(readFileSync(new URL('../../docs/assets/performance-samples.json.gz', import.meta.url))));
export const now = Date.parse('2026-09-21T06:30:00Z') / 1000;
const wave = (i, phase = 0) => Math.sin(i * .13 + phase) * 5 + Math.sin(i * .47 + phase) * 2;
export const metricIds = ['sm_active', 'sm_occupancy', 'tensor_active', 'dram_bw_active', 'fp16_non_tensor_active', 'fp32_non_tensor_active', 'fp64_non_tensor_active', 'pcie_tx_per_second', 'pcie_rx_per_second', 'nvlink_tx_per_second', 'nvlink_rx_per_second'].map(x => `nvidia.gpm.${x}`);
const metricBase = [86, 63, 74, 68, 3, 18, .4, 4200, 6700, 85000, 79000];
const users = ['demo-train', 'demo-vision', 'demo-infer', 'demo-eval'];
const tasks = ['train_transformer.py', 'finetune_vision.py', 'batch_inference.py', 'evaluate_model.py'];
function totals(gpus) {
  const sum = key => gpus.reduce((n, g) => n + g[key], 0);
  return { gpu_count: gpus.length, accelerator_count: gpus.length, card_count: gpus.length,
    avg_gpu_utilization: sum('utilization_gpu') / gpus.length, avg_memory_utilization: sum('utilization_mem') / gpus.length,
    memory_used_mb: sum('memory_used_mb'), memory_total_mb: sum('memory_total_mb'), power_watts: sum('power_watts'), power_limit_watts: sum('power_limit_watts'),
    max_temperature_c: Math.max(...gpus.map(g => g.temperature_c)), active_processes: gpus.reduce((n, g) => n + g.processes.length, 0) };
}
export const nodes = [
  ['demo-train-01', 'NVIDIA H100 80GB HBM3', 4, 80, 88],
  ['demo-train-02', 'NVIDIA A100-SXM4-80GB', 4, 80, 76],
  ['demo-infer-01', 'NVIDIA L40S', 4, 48, 52],
  ['demo-npu-01', 'Ascend 910B', 4, 64, 65],
].map(([id, name, count, memory, load], n) => {
  const gpus = Array.from({length: count}, (_, i) => {
    const used = Math.round(memory * 1024 * (.73 - i * .035));
    const metrics = Object.fromEntries(metricIds.map((m, j) => [m, metricBase[j] * (1 - i * .025)]));
    return {index: i, uuid: `DEMO-${n}-${i}`, gpu_id: `${id}:DEMO-${n}-${i}`, node_id: id,
      name, device_type: n === 3 ? 'ascend' : 'nvidia', card_id: String(i), architecture: ['Hopper', 'Ampere', 'Ada Lovelace', 'Da Vinci'][n],
      utilization_gpu: load - i * 3, utilization_mem: 63 - i * 2, memory_total_mb: memory * 1024,
      memory_used_mb: used, memory_free_mb: memory * 1024 - used, memory_percent: used / (memory * 1024) * 100,
      temperature_c: 64 + i, power_watts: 420 - n * 55 - i * 8, power_limit_watts: n < 2 ? 700 : 350,
      power_percent: (420 - n * 55 - i * 8) / (n < 2 ? 700 : 350) * 100, clock_sm_mhz: 1830, clock_mem_mhz: 2619, pstate: 'P0',
      performance: n < 2 ? {profile: 'nvidia.gpm.v1', status: 'available', sampled_at: now, interval_ms: 1000, metrics, supported_metrics: metricIds} : null,
      processes: [{pid: 42000 + n * 100 + i, name: 'python', task_name: tasks[n], user: users[n], kind: 'C', gpu_memory_mb: used - 512,
        cmdline: `python ${tasks[n]} --config demo.yaml`, runtime_seconds: 7640 - i * 180, process_start_time: now - 7640 + i * 180, detail_status: 'available'}], other_users: []};
  });
  return {node_id: id, hostname: id, seq: 1, sampled_at: now, received_at: now, refresh_interval: 1, process_interval: 5,
    status: 'online', elapsed_ms: 12 + n * 3, source: n === 3 ? 'dcmi' : 'nvml', gpus, totals: totals(gpus), agent_version: '0.1.4',
    performance_profiles: n < 2 ? ['nvidia.gpm.v1'] : [],
    hardware: {gpus: gpus.map(g => ({index: g.index, uuid: g.uuid, name: g.name, architecture: g.architecture}))},
    history: Object.fromEntries(gpus.map((g, i) => [g.gpu_id, {gpu: Array.from({length: 90}, (_, t) => load - i * 3 + wave(t, i * .3))}]))};
});
// Keep visible node metrics consistent with the real performance samples.
for (const [i, gpu] of nodes[0].gpus.entries()) {
  const series = captured.series[i];
  gpu.performance.metrics = Object.fromEntries(Object.entries(series.metrics).map(([id, metric]) => [id, metric.points.findLast(p => p[1] != null)?.[1] ?? 0]));
  gpu.performance.supported_metrics = Object.keys(series.metrics);
}
export const snapshot = {ok: true, seq: 1, timestamp: now, nodes,
  totals: {...totals(nodes.flatMap(n => n.gpus)), node_count: 4, online_node_count: 4, stale_node_count: 0, offline_node_count: 0}, history: {}};
export const jobs = Array.from({length: 5}, (_, i) => ({job_key: `demo-job-${i}`, user: 'demo-train', node_id: nodes[i % 2].node_id,
  task_name: ['train_transformer.py', 'train_adapter.py', 'train_vision.py', 'train_embeddings.py', 'train_baseline.py'][i],
  started_at: now - 9000 - i * 14400, last_seen_at: now - 1800 - i * 14400, duration_seconds: 7200, status: 'completed', session_count: 4, gpu_count: 4,
  pids: [42000 + i * 10, 42001 + i * 10], gpus: nodes[i % 2].gpus.map(g => ({node_id: g.node_id, gpu_uuid: g.uuid, gpu_index: g.index, gpu_name: g.name}))}));
function jobCurve(job) {
  return {enabled: true, source: 'rollup', job_key: job.job_key, job, range_start: job.started_at, range_end: job.last_seen_at,
    coverage_start: job.started_at, coverage_end: job.last_seen_at, resolution_seconds: 20, resolution_mode: '20s', warnings: [],
    series: job.gpus.map((g, k) => ({...g, label: `GPU${k}`, points: Array.from({length: 361}, (_, i) => {
      const ramp = Math.min(1, i / 22, (360 - i) / 10);
      const util = Math.max(0, (87 - k * 2 + wave(i, k * .3) - (i % 75 > 68 ? 25 : 0)) * ramp);
      return {bucket_start: job.started_at + i * 20, avg_gpu_utilization: util, max_gpu_utilization: util + 2,
        avg_memory_used_mb: 58000 * ramp, avg_power_watts: 430 * ramp, avg_temperature_c: 65, sample_count: 20};
    })}))};
}
export function apiResponse(url) {
  const p = url.pathname;
  if (p === '/api/settings') return {refresh_interval: 1, process_interval: 5, allowed_refresh_intervals: [1, 2, 5]};
  if (p === '/api/cluster/snapshot') return snapshot;
  if (p === '/api/analytics/overview') return {enabled: true, range_start: now - 86400, range_end: now, generated_at: now,
    user_gpu_hours: users.map((user, i) => ({user, gpu_hours: 68 - i * 12, weighted_gpu_hours: 68 - i * 12, job_count: 12 - i * 2, last_seen_at: now - i * 120, top_gpu_models: [{name: nodes[i].gpus[0].name, gpu_hours: 68 - i * 12}]})),
    job_rankings: jobs.slice(0, 4).map((j, i) => ({...j, weighted_gpu_hours: 28 - i * 4})), anomalies: [], off_hours: {night_job_count: 8, weekend_job_count: 4, night_gpu_hours: 64, weekend_gpu_hours: 32, top_users: []}};
  if (p.startsWith('/api/analytics/node/')) return {enabled: true, series: [], heatmap: []};
  if (p === '/api/highres/jobs') {
    const query = (url.searchParams.get('q') || '').toLowerCase();
    return {enabled: true, items: jobs.filter(j => `${j.user} ${j.task_name}`.includes(query))};
  }
  if (p.startsWith('/api/highres/jobs/')) return jobCurve(jobs.find(j => p.includes(j.job_key)) || jobs[0]);
  if (p === '/api/highres/performance') {
    const selected = (url.searchParams.get('gpu_uuid') || '').split(',');
    const requested = (url.searchParams.get('metrics') || '').split(',');
    return {...captured, metrics: requested, series: captured.series.filter(g => selected.includes(g.gpu_uuid)).map(g => ({...g,
      metrics: Object.fromEntries(Object.entries(g.metrics).filter(([id]) => requested.includes(id)))}))};
  }
  throw new Error(`Unmocked demo endpoint: ${p}`);
}
