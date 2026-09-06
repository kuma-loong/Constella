import { compactGpuName } from "../cluster-utils";
import type { ClusterSnapshot, GpuProcess } from "../types";
import type { LabUser } from "./types";

export type ActivityStatus = "running" | "ended" | "unknown";
export type ActivityJob = {
  job_key: string; node_id: string; user: string; task_name: string;
  pids: number[]; started_at: number; last_seen_at: number; status: ActivityStatus;
  sessions: { pid: number; process_start_time: number | null }[];
  models: string[]; gpu_count: number;
  intervals: { start: number; end: number; model: string; card: string }[];
  command?: string;
  segments?: { start: number; end: number; count: number }[];
};
export type ActivitySummary = { gpu_hours: number; active_hours: number; active_days: number };
export type ActivityDay = ActivitySummary & { date: string; start: number; end: number; segments: { start: number; end: number; count: number }[] };
export type Activity = {
  enabled: boolean; availability: string; as_of: number; range_start: number;
  missing_uid_sessions: number; summary: ActivitySummary; selection: ActivitySummary;
  days: ActivityDay[]; gpu_models: { model: string; gpu_hours: number; share: number }[];
  busiest_hours: { start_hour: number; end_hour: number } | null;
  jobs: { items: ActivityJob[]; next_cursor: string | null; total: number };
  day_jobs: { items: ActivityJob[]; total: number };
};
export const hours = (n: number) => n.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
export const clockHour = (n: number) => `${String(n).padStart(2, "0")}:00`;
export function dateLabel(date: string, weekday = false) {
  return new Date(`${date}T00:00:00+08:00`).toLocaleDateString("en-US", { timeZone: "Asia/Shanghai", ...(weekday ? { weekday: "short" } : { month: "short", day: "numeric" }) });
}
export const jobsLink = (job: ActivityJob) => `/jobs?${new URLSearchParams({ job_key: job.job_key, node_id: job.node_id, range: "30d" })}`;
export const statusLabel = (status: ActivityStatus) => status === "unknown" ? "Status unavailable" : status === "running" ? "Running" : "Ended";

// Match the existing Jobs identity, including its ten-minute parent grouping window.
function liveKey(node: string, p: GpuProcess): string | null {
  if (p.process_start_time == null) return null;
  const parent = p.ppid != null && p.parent_start_time != null && p.process_start_time >= p.parent_start_time && p.process_start_time - p.parent_start_time <= 600;
  const start = parent ? p.parent_start_time! : p.process_start_time;
  const timestamp = Number.isInteger(start) ? `${start}.0` : String(start);
  return `${node}:${p.user || "unknown"}:${timestamp}:${parent ? p.ppid : p.pid}`;
}

export function liveJobs(snapshot: ClusterSnapshot | null, user: LabUser): ActivityJob[] {
  const result = new Map<string, ActivityJob>();
  const now = Date.now() / 1000;
  for (const node of snapshot?.nodes || []) {
    if (node.status !== "online" || now - node.sampled_at > Math.max(30, 3 * node.process_interval)) continue;
    const bindings = user.bindings.filter(b => b.node_id === node.node_id && b.valid_from <= node.sampled_at && (!b.valid_to || node.sampled_at < b.valid_to));
    for (const gpu of node.gpus) for (const p of gpu.processes) {
      if (p.user_uid == null || !bindings.some(b => b.unix_uid === p.user_uid)) continue;
      const key = liveKey(node.node_id, p);
      if (!key) continue;
      let job = result.get(key);
      if (!job) {
        job = { job_key: key, node_id: node.node_id, user: p.user || "Unknown", task_name: p.task_name || p.name,
          pids: [], sessions: [], started_at: p.process_start_time!, last_seen_at: node.sampled_at,
          status: "running", models: [], gpu_count: 0, intervals: [], command: p.cmdline || p.exe || undefined };
        result.set(key, job);
      }
      if (!job.pids.includes(p.pid)) {
        job.pids.push(p.pid);
        job.sessions.push({ pid: p.pid, process_start_time: p.process_start_time! });
      }
      job.started_at = Math.min(job.started_at, p.process_start_time!);
      const model = compactGpuName(gpu.name);
      if (!job.models.includes(model)) job.models.push(model);
      const card = gpu.device_type === "ascend" && gpu.card_id != null ? `${node.node_id}:ascend:${gpu.card_id}` : `${node.node_id}:${gpu.uuid}`;
      if (!job.intervals.some(r => r.card === card)) job.intervals.push({ start: node.sampled_at, end: node.sampled_at, model, card });
      job.gpu_count = job.intervals.length;
    }
  }
  return [...result.values()];
}

export function mergeLiveJobs(history: ActivityJob[], live: ActivityJob[], snapshot: ClusterSnapshot | null): ActivityJob[] {
  const combined = new Map(history.map(job => [job.job_key, job]));
  const now = Date.now() / 1000;
  for (const job of history) {
    const node = snapshot?.nodes.find(n => n.node_id === job.node_id);
    if (!node) continue;
    const fresh = node.status === "online" && now - node.sampled_at < Math.max(30, 3 * node.process_interval);
    const status = fresh && node.gpus.length > 0 && !node.error && !node.gpus.some(g => g.error || g.other_users.length) ? "ended" : job.status === "ended" ? "ended" : "unknown";
    combined.set(job.job_key, { ...job, status });
  }
  for (const job of live) {
    const previous = combined.get(job.job_key);
    combined.set(job.job_key, { ...previous, ...job, started_at: previous?.started_at || job.started_at,
      intervals: previous?.intervals || job.intervals });
  }
  return [...combined.values()].sort((a, b) => Number(b.status === "running") - Number(a.status === "running") || b.last_seen_at - a.last_seen_at || a.job_key.localeCompare(b.job_key));
}
