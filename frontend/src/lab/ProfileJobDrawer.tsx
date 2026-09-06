import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import uPlot from "uplot";
import { Icon } from "./ProfileIcon";
import { fmtDuration, fmtGiB, fmtMiBPerSecond, formatTime } from "../format";
import { PERFORMANCE_GROUPS } from "../performance-metrics";
import type { ClusterSnapshot } from "../types";
import { labRequest } from "./api";
import { jobsLink, statusLabel, type ActivityJob } from "./profile-data";

type JobDetail = { sessions: { pid: number; ppid: number | null; cmdline_text: string | null; exe: string | null }[]; gpus: { gpu_uuid: string; gpu_index: number; gpu_name: string }[] };
type Curve = {
  enabled: boolean; source?: string; resolution_seconds?: number; concurrent_session_count?: number;
  series?: { gpu_uuid: string; gpu_index: number; label?: string; gpu_name?: string;
    points?: ({ bucket_start: number } & Record<string, number | null>)[];
    metrics?: Record<string, { points: [number, number | null][] }>; }[];
};
type Line = { label: string; points: [number, number | null][] };
const BASE_METRICS = [
  { id: "avg_gpu_utilization", label: "GPU utilization", unit: "percent" },
  { id: "avg_memory_used_mb", label: "GPU memory", unit: "memory" },
  { id: "avg_power_watts", label: "Power", unit: "watts" },
  { id: "avg_temperature_c", label: "Temperature", unit: "temperature" },
];
const PERFORMANCE_METRICS = PERFORMANCE_GROUPS.flatMap(group => group.metrics);

export function ProfileJobDrawer({ job, snapshot, onClose }: { job: ActivityJob; snapshot: ClusterSnapshot | null; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState("overview");
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [curve, setCurve] = useState<Curve | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [copied, setCopied] = useState("");
  const [metric, setMetric] = useState(BASE_METRICS[0].id);
  const [retry, setRetry] = useState(0);
  const endpoint = `/api/highres/jobs/${encodeURIComponent(job.job_key)}`;
  const node = snapshot?.nodes.find(n => n.node_id === job.node_id);
  const relevant = node?.gpus.filter(g => detail?.gpus.some(d => d.gpu_uuid === g.uuid) || g.processes.some(p => job.pids.includes(p.pid))) || [];
  const supported = relevant.flatMap(g => g.performance?.supported_metrics || []);
  const performanceMetrics = PERFORMANCE_METRICS.filter(m => !m.id.includes("nvlink") || supported.includes(m.id));
  const options = tab === "performance" ? performanceMetrics : BASE_METRICS;
  const selectedMetric = options.find(m => m.id === metric) || options[0];
  const requestedMetrics = tab === "performance" ? selectedMetric.id : "";
  const command = job.command || detail?.sessions.find(s => s.cmdline_text)?.cmdline_text || detail?.sessions.find(s => s.exe)?.exe;

  useEffect(() => {
    const element = dialog.current!;
    const previousFocus = document.activeElement as HTMLElement | null;
    element.showModal();
    return () => { element.close(); previousFocus?.focus(); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void labRequest<{ item?: JobDetail }>(endpoint, { signal: controller.signal }).then(value => setDetail(value.item || null)).catch(() => {});
    return () => controller.abort();
  }, [endpoint, retry]);
  useEffect(() => {
    if (tab === "overview") return;
    const controller = new AbortController();
    setLoading(true); setCurve(null); setMessage("");
    const params = new URLSearchParams({ resolution: "auto", ...(tab === "performance" ? { metrics: requestedMetrics } : {}) });
    void labRequest<Curve>(`${endpoint}/${tab}?${params}`, { signal: controller.signal }).then(setCurve).catch(() => {
      if (!controller.signal.aborted) setMessage("Measurements are unavailable. A new job may take a little time to appear in history.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [endpoint, tab, requestedMetrics, retry]);
  const lines = useMemo<Line[]>(() => (curve?.series || []).map(series => ({ label: `GPU ${series.gpu_index}${series.gpu_name ? ` · ${series.gpu_name}` : ""}`,
    points: tab === "performance" ? series.metrics?.[selectedMetric.id]?.points || [] : (series.points || []).map((p): [number, number | null] => [p.bucket_start, p[selectedMetric.id] ?? null]),
  })).filter(line => line.points.some(([, v]) => v != null)), [curve, tab, selectedMetric.id]);

  return <dialog class="process-drawer profile-drawer" ref={dialog} aria-labelledby="profile-detail-title" onCancel={e => { e.preventDefault(); onClose(); }} onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
    <header class="process-drawer-head"><div><span>{statusLabel(job.status)} · {job.node_id}</span><h2 id="profile-detail-title">{job.task_name}</h2></div><button class="guide-close" autoFocus aria-label="Close job details" onClick={onClose}><Icon name="x" /></button></header>
    <div class="profile-detail-tabs" role="group" aria-label="Job detail views">{["overview", "gpu", "performance"].map(value => <button aria-pressed={tab === value} onClick={() => { setTab(value); setMetric(value === "performance" ? PERFORMANCE_METRICS[0].id : BASE_METRICS[0].id); }}>{value === "gpu" ? "GPU" : value[0].toUpperCase() + value.slice(1)}</button>)}</div>
    <div class="process-drawer-body">
      {tab === "overview" ? <><dl class="process-meta">{[["Account", job.user], ["Node", job.node_id], ["PIDs", job.pids.join(", ")], ["Duration", fmtDuration(job.last_seen_at - job.started_at)], ["First observed", formatTime(job.started_at)], ["Last observed", formatTime(job.last_seen_at)], ["GPUs", `${job.gpu_count} · ${job.models.join(" / ")}`], ["Status", statusLabel(job.status)]].map(([label, value]) => <div><dt>{label}</dt><dd>{value}</dd></div>)}</dl><section class="process-drawer-section"><h3>Command</h3><code class="process-command">{command || "No command recorded for this job."}</code></section></> : <>
        <label class="profile-metric">Metric<select value={selectedMetric.id} onChange={e => setMetric(e.currentTarget.value)}>{tab === "performance" ? PERFORMANCE_GROUPS.map(group => <optgroup label={group.label}>{group.metrics.filter(m => performanceMetrics.includes(m)).map(m => <option value={m.id}>{m.label}</option>)}</optgroup>) : options.map(m => <option value={m.id}>{m.label}</option>)}</select></label>
        {loading ? <p role="status" class="profile-curve-note">Loading measurements…</p> : message ? <div class="profile-curve-note" role="alert"><p>{message}</p><button class="process-action" onClick={() => setRetry(v => v + 1)}>Retry</button></div> : lines.length ? <ProfileCurve lines={lines} unit={selectedMetric.unit} /> : <p class="profile-curve-note">No recorded measurements for this metric and time window.</p>}
        <p class="profile-curve-note">{curve?.resolution_seconds === 3600 ? "Hourly measurements. Each point covers a full hour and may include time outside this job." : curve?.source === "high_res_memory" ? "High-resolution measurements." : curve?.resolution_seconds ? `${curve.resolution_seconds.toLocaleString("en-US", { maximumFractionDigits: 1 })}-second measurements.` : ""} GPU measurements describe the whole device; other jobs may contribute.</p>
        {!!curve?.concurrent_session_count && <p class="profile-curve-note">Other jobs shared these GPUs during this time.</p>}
      </>}
    </div><footer class="process-drawer-actions"><a class="process-action is-primary" href={jobsLink(job)} onClick={onClose}>Explore in Jobs<Icon name="arrow-up-right" /></a><button class="process-action" disabled={!command} onClick={() => { if (command) void navigator.clipboard.writeText(command).then(() => setCopied("Copied")).catch(() => setCopied("Copy failed")); }}>{copied || "Copy command"}</button><span role="status" class="sr-only">{copied}</span></footer>
  </dialog>;
}

function ProfileCurve({ lines, unit }: { lines: Line[]; unit: string }) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = host.current!;
    let plot: uPlot | null = null;
    const values = (v: number | null | undefined) => v == null ? "—" : unit === "memory" ? fmtGiB(v) : unit === "mib_per_second" ? fmtMiBPerSecond(v) : `${v.toFixed(1)}${unit === "percent" ? "%" : unit === "watts" ? " W" : " °C"}`;
    const timestamps = [...new Set(lines.flatMap(line => line.points.map(([t]) => t)))].sort((a, b) => a - b);
    const data: uPlot.AlignedData = [timestamps, ...lines.map(line => { const points = new Map(line.points); return timestamps.map(t => points.get(t) ?? null); })];
    const render = () => {
      plot?.destroy();
      const style = getComputedStyle(document.documentElement);
      const muted = style.getPropertyValue("--muted").trim();
      const cobalt = style.getPropertyValue("--telemetry").trim();
      plot = new uPlot({ width: Math.max(200, element.clientWidth), height: 265, padding: [12, 12, 0, 0],
        scales: { x: { time: true } },
        axes: [{ stroke: muted, grid: { show: false }, values: (_p, ticks) => ticks.map(t => new Date(t * 1000).toLocaleTimeString("en-GB", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit" })) },
          { size: unit === "mib_per_second" ? 94 : 65, stroke: muted, grid: { stroke: style.getPropertyValue("--chart-grid").trim() }, values: (_p, ticks) => ticks.map(values) }],
        series: [{ label: "UTC+8", value: (_p, v) => v == null ? "—" : formatTime(v) }, ...lines.map((line, i) => ({ label: line.label, stroke: cobalt, width: 1.5, dash: i ? [3 + i * 2, 3] : [], points: { show: timestamps.length < 3, size: 5 }, spanGaps: false, value: (_p: uPlot, v: number | null | undefined) => values(v) }))],
      }, data, element);
    };
    render();
    const resize = new ResizeObserver(() => plot?.setSize({ width: Math.max(200, element.clientWidth), height: 265 })); resize.observe(element);
    const theme = new MutationObserver(render); theme.observe(document.documentElement, { attributes: true, attributeFilter: ["data-resolved-theme"] });
    return () => { resize.disconnect(); theme.disconnect(); plot?.destroy(); };
  }, [lines, unit]);
  return <div class="profile-curve" ref={host} role="img" aria-label={`Recorded GPU measurements for ${lines.length} GPU${lines.length === 1 ? "" : "s"}. Move over the chart to inspect values.`} />;
}
