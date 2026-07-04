import {
  Activity,
  AlertTriangle,
  BarChart3,
  Clock3,
  Cpu,
  Database,
  Gauge,
  LineChart,
  ListTree,
  Moon,
  Pause,
  Play,
  RefreshCw,
  Server,
  Table2,
  Thermometer,
  Users,
  Zap,
  createIcons,
} from "lucide";
import "./styles.css";

const iconSet = {
  Activity,
  AlertTriangle,
  BarChart3,
  Clock3,
  Cpu,
  Database,
  Gauge,
  LineChart,
  ListTree,
  Moon,
  Pause,
  Play,
  RefreshCw,
  Server,
  Table2,
  Thermometer,
  Users,
  Zap,
};

type GpuProcess = {
  pid: number;
  name: string;
  ppid?: number | null;
  task_name?: string | null;
  exe?: string | null;
  cmdline?: string | null;
  cmdline_hash?: string | null;
  gpu_memory_mb: number;
  user?: string | null;
  kind: string;
  runtime_seconds?: number | null;
  process_start_time?: number | null;
  parent_start_time?: number | null;
  detail_status?: string | null;
  detail_error?: string | null;
};

type OtherUserMemory = {
  user: string;
  process_count: number;
  total_memory_mb: number;
  runtime_seconds?: number | null;
};

type GpuHardwareInfo = {
  index: number;
  uuid: string;
  name: string;
  architecture?: string | null;
};

type NodeHardware = {
  gpus: GpuHardwareInfo[];
};

type GpuInfo = {
  index: number;
  node_id?: string | null;
  gpu_id?: string | null;
  uuid: string;
  name: string;
  pci_bus_id?: string | null;
  utilization_gpu: number;
  utilization_mem: number;
  memory_total_mb: number;
  memory_used_mb: number;
  memory_free_mb: number;
  memory_percent: number;
  temperature_c: number;
  power_watts: number;
  power_limit_watts: number;
  power_percent: number;
  clock_sm_mhz?: number | null;
  clock_mem_mhz?: number | null;
  max_clock_sm_mhz?: number | null;
  max_clock_mem_mhz?: number | null;
  pstate?: string | null;
  compute_mode?: string | null;
  mig_mode?: string | null;
  ecc_mode?: string | null;
  processes: GpuProcess[];
  other_users: OtherUserMemory[];
  error?: string | null;
};

type NodeTotals = {
  gpu_count: number;
  avg_gpu_utilization: number;
  avg_memory_utilization: number;
  memory_used_mb: number;
  memory_total_mb: number;
  power_watts: number;
  power_limit_watts: number;
  max_temperature_c: number;
  active_processes: number;
};

type NodeSnapshot = {
  node_id: string;
  hostname: string;
  seq: number;
  sampled_at: number;
  received_at?: number | null;
  refresh_interval: number;
  process_interval: number;
  status: "online" | "stale" | "offline" | "error" | string;
  source: string;
  gpus: GpuInfo[];
  totals: NodeTotals;
  error?: string | null;
  agent_version?: string | null;
  driver_version?: string | null;
  cuda_driver_version?: string | null;
  nvml_version?: string | null;
  elapsed_ms?: number;
  history: Record<string, Record<string, number[]>>;
  hardware?: NodeHardware | null;
};

type ClusterSnapshot = {
  ok: boolean;
  seq: number;
  timestamp: number;
  nodes: NodeSnapshot[];
  totals: NodeTotals & {
    node_count: number;
    online_node_count: number;
    stale_node_count: number;
    offline_node_count: number;
  };
  history: Record<string, Record<string, number[]>>;
};

type Settings = {
  refresh_interval: number;
  allowed_refresh_intervals: number[];
  process_interval: number;
};

type AnalyticsMeta = {
  enabled: boolean;
  generated_at?: number;
  range_start?: number;
  range_end?: number;
  timezone?: string;
  bucket_seconds?: number;
};

type OverviewAnalytics = AnalyticsMeta & {
  user_gpu_hours?: UserGpuHours[];
  job_rankings?: JobRanking[];
  anomalies?: AnomalyItem[];
  off_hours?: OffHours;
};

type UserGpuHours = {
  user: string;
  gpu_hours: number;
  weighted_gpu_hours: number;
  task_count: number;
  job_count: number;
  last_seen_at: number;
  top_gpu_models: { name: string; gpu_hours: number }[];
};

type JobRanking = {
  job_key: string;
  user: string;
  node_id: string;
  task_name: string;
  started_at: number;
  last_seen_at: number;
  duration_seconds: number;
  gpu_count: number;
  session_count: number;
  gpu_hours: number;
  weighted_gpu_hours: number;
  status: string;
};

type AnomalyItem = {
  user: string;
  node_id: string;
  task_name: string;
  duration_seconds: number;
  gpu_memory_gb: number;
  recent_avg_gpu_utilization: number;
  idle_tail_seconds: number;
  gpu_uuids: string[];
  last_seen_at: number;
  reason: string;
};

type OffHours = {
  night_job_count: number;
  weekend_job_count: number;
  night_gpu_hours: number;
  weekend_gpu_hours: number;
  top_users: { user: string; job_count: number }[];
};

type NodeAnalytics = AnalyticsMeta & {
  node_id?: string;
  gpus?: AnalyticsGpu[];
  series?: AnalyticsSeries[];
  heatmap?: AnalyticsHeatmap[];
  heatmap_bucket_seconds?: number;
};

type AnalyticsGpu = {
  uuid: string;
  gpu_index: number;
  name: string;
  memory_total_mb: number;
};

type AnalyticsPoint = {
  bucket_start: number;
  avg_gpu_utilization: number;
  max_gpu_utilization: number;
  avg_memory_used_mb: number;
  max_memory_used_mb: number;
  avg_power_watts: number;
  max_power_watts: number;
  avg_temperature_c: number;
  max_temperature_c: number;
  sample_count: number;
};

type AnalyticsSeries = {
  gpu_uuid: string;
  gpu_index: number | null;
  gpu_name: string | null;
  points: AnalyticsPoint[];
};

type AnalyticsHeatmap = {
  gpu_uuid: string;
  gpu_index: number | null;
  gpu_name: string | null;
  buckets: Pick<AnalyticsPoint, "bucket_start" | "avg_gpu_utilization" | "max_gpu_utilization" | "avg_memory_used_mb" | "sample_count">[];
};

type NodeMetric = "avg_gpu_utilization" | "avg_memory_used_mb" | "avg_power_watts" | "avg_temperature_c";

type Route = { kind: "overview" } | { kind: "node"; nodeId: string };

const DEFAULT_REFRESH_INTERVALS = [0.5, 1, 2, 5];
const OVERVIEW_ANALYTICS_RANGES = ["24h", "7d", "30d"];
const NODE_ANALYTICS_RANGES = ["1h", "24h", "7d", "30d"];
const NODE_METRICS: { key: NodeMetric; label: string; unit: string; max?: number }[] = [
  { key: "avg_gpu_utilization", label: "GPU", unit: "%", max: 100 },
  { key: "avg_memory_used_mb", label: "Memory", unit: "GiB" },
  { key: "avg_power_watts", label: "Power", unit: "W" },
  { key: "avg_temperature_c", label: "Temp", unit: "C", max: 100 },
];
const CHART_COLORS = ["#1f9d72", "#2563eb", "#c2410c", "#7c3aed", "#be123c", "#0f766e", "#b45309", "#4f46e5"];

const summaryGrid = mustGet<HTMLElement>("summaryGrid");
const gpuGrid = mustGet<HTMLElement>("gpuGrid");
const fabricBand = mustGet<HTMLElement>("fabricBand");
const overviewAnalytics = mustGet<HTMLElement>("overviewAnalytics");
const nodeHistorySection = mustGet<HTMLElement>("nodeHistorySection");
const processSection = mustGet<HTMLElement>("processSection");
const processRows = mustGet<HTMLElement>("processRows");
const processMeta = mustGet<HTMLElement>("processMeta");
const liveState = mustGet<HTMLElement>("liveState");
const nodeLine = mustGet<HTMLElement>("nodeLine");
const appRoot = mustGet<HTMLElement>("app");
const topNav = mustGet<HTMLElement>("topNav");
const refreshControl = mustGet<HTMLElement>("refreshControl");
const pauseButton = mustGet<HTMLButtonElement>("pauseButton");
const refreshButton = mustGet<HTMLButtonElement>("refreshButton");

let socket: WebSocket | null = null;
let reconnectTimer = 0;
let paused = false;
let lastSnapshot: ClusterSnapshot | null = null;
let lastSettings: Settings | null = null;
let currentRefreshInterval: number | null = null;
let refreshPending = false;
let overviewRange = "7d";
let overviewAnalyticsPayload: OverviewAnalytics | null = null;
let overviewAnalyticsKey = "";
let overviewAnalyticsLoading = false;
let nodeRange = "24h";
let nodeMetric: NodeMetric = "avg_gpu_utilization";
let nodeAnalyticsPayload: NodeAnalytics | null = null;
let nodeAnalyticsKey = "";
let nodeAnalyticsLoading = false;

pauseButton.addEventListener("click", () => {
  paused = !paused;
  pauseButton.innerHTML = paused ? icon("play") : icon("pause");
  pauseButton.setAttribute("aria-label", paused ? "Resume stream" : "Pause stream");
  pauseButton.setAttribute("title", paused ? "Resume stream" : "Pause stream");
  setLiveState(paused ? "paused" : socket?.readyState === WebSocket.OPEN ? "live" : "connecting");
  createIcons({ icons: iconSet });
});

refreshButton.addEventListener("click", () => {
  fetchSnapshot();
});

refreshControl.addEventListener("click", (event) => {
  const target = (event.target as HTMLElement).closest("[data-refresh-interval]") as HTMLButtonElement | null;
  if (!target || target.disabled) {
    return;
  }
  const interval = Number(target.dataset.refreshInterval);
  if (Number.isFinite(interval)) {
    setRefreshInterval(interval);
  }
});

appRoot.addEventListener("click", (event) => {
  const target = (event.target as HTMLElement).closest("[data-analytics-action]") as HTMLButtonElement | null;
  if (!target || target.disabled) {
    return;
  }
  const action = target.dataset.analyticsAction;
  if (action === "overview-range" && target.dataset.range) {
    overviewRange = target.dataset.range;
    overviewAnalyticsPayload = null;
    overviewAnalyticsKey = "";
    renderOverviewAnalytics();
    fetchOverviewAnalytics();
  }
  if (action === "node-range" && target.dataset.range) {
    nodeRange = target.dataset.range;
    nodeAnalyticsPayload = null;
    nodeAnalyticsKey = "";
    renderNodeHistory(currentRoute());
    fetchNodeAnalytics(currentRoute());
  }
  if (action === "node-metric" && target.dataset.metric) {
    nodeMetric = target.dataset.metric as NodeMetric;
    renderNodeHistory(currentRoute());
  }
  if (action === "overview-refresh") {
    overviewAnalyticsKey = "";
    fetchOverviewAnalytics();
  }
  if (action === "node-refresh") {
    nodeAnalyticsKey = "";
    fetchNodeAnalytics(currentRoute());
  }
});

appRoot.addEventListener("click", (event) => {
  const link = (event.target as HTMLElement).closest("a[href]") as HTMLAnchorElement | null;
  if (!shouldHandleAppLink(event, link)) {
    return;
  }
  event.preventDefault();
  navigateTo(link.pathname);
});

window.addEventListener("popstate", () => {
  renderCurrentRoute();
});

renderRefreshControl(DEFAULT_REFRESH_INTERVALS, null);
normalizeInitialRoute();
renderNav(null, currentRoute());
fetchSettings();
connect();
fetchSnapshot();
createIcons({ icons: iconSet });

function mustGet<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing element: ${id}`);
  }
  return element as T;
}

function normalizeInitialRoute() {
  if (window.location.pathname === "/") {
    window.history.replaceState(null, "", "/overview");
  }
}

function currentRoute(): Route {
  const path = window.location.pathname.replace(/\/+$/, "") || "/overview";
  if (path.startsWith("/nodes/")) {
    const encoded = path.slice("/nodes/".length);
    return { kind: "node", nodeId: decodeURIComponent(encoded) };
  }
  return { kind: "overview" };
}

function isAppPath(pathname: string) {
  return pathname === "/" || pathname === "/overview" || pathname.startsWith("/nodes/");
}

function shouldHandleAppLink(event: MouseEvent, link: HTMLAnchorElement | null): link is HTMLAnchorElement {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  ) {
    return false;
  }
  if (!link || link.origin !== window.location.origin || !isAppPath(link.pathname)) {
    return false;
  }
  return !link.target && !link.hasAttribute("download");
}

function navigateTo(pathname: string) {
  const normalized = pathname === "/" ? "/overview" : pathname;
  if (normalized !== window.location.pathname) {
    window.history.pushState(null, "", normalized);
  }
  renderCurrentRoute();
}

function renderCurrentRoute() {
  if (lastSnapshot) {
    render(lastSnapshot);
    return;
  }
  renderNav(null, currentRoute());
}

function connect() {
  window.clearTimeout(reconnectTimer);
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${window.location.host}/ws/cluster`);
  setLiveState("connecting");

  socket.addEventListener("open", () => {
    setLiveState(paused ? "paused" : "live");
  });

  socket.addEventListener("message", (event) => {
    const snapshot = JSON.parse(event.data) as ClusterSnapshot;
    lastSnapshot = snapshot;
    if (!paused) {
      render(snapshot);
    }
  });

  socket.addEventListener("close", () => {
    setLiveState("offline");
    reconnectTimer = window.setTimeout(connect, 1200);
  });

  socket.addEventListener("error", () => {
    setLiveState("offline");
  });
}

async function fetchSnapshot() {
  try {
    const response = await fetch("/api/cluster/snapshot", { cache: "no-store" });
    const snapshot = (await response.json()) as ClusterSnapshot;
    lastSnapshot = snapshot;
    render(snapshot);
  } catch {
    setLiveState("offline");
  }
}

async function fetchSettings() {
  try {
    const response = await fetch("/api/settings", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`settings request failed: ${response.status}`);
    }
    const settings = (await response.json()) as Settings;
    lastSettings = settings;
    renderRefreshControl(settings.allowed_refresh_intervals, settings.refresh_interval);
  } catch {
    syncRefreshControl(clusterRefreshInterval(lastSnapshot) ?? currentRefreshInterval);
  }
}

async function setRefreshInterval(interval: number) {
  if (refreshPending || sameInterval(interval, currentRefreshInterval)) {
    return;
  }
  const previous = currentRefreshInterval;
  refreshPending = true;
  syncRefreshControl(interval);
  try {
    const response = await fetch("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_interval: interval }),
    });
    if (!response.ok) {
      throw new Error(`settings update failed: ${response.status}`);
    }
    const settings = (await response.json()) as Settings;
    lastSettings = settings;
    renderRefreshControl(settings.allowed_refresh_intervals, settings.refresh_interval);
  } catch {
    syncRefreshControl(clusterRefreshInterval(lastSnapshot) ?? lastSettings?.refresh_interval ?? previous);
  } finally {
    refreshPending = false;
    syncRefreshControl(currentRefreshInterval);
  }
}

async function fetchOverviewAnalytics() {
  const key = overviewRange;
  if (overviewAnalyticsLoading || overviewAnalyticsKey === key) {
    return;
  }
  overviewAnalyticsLoading = true;
  renderOverviewAnalytics();
  try {
    const response = await fetch(`/api/analytics/overview?range=${encodeURIComponent(overviewRange)}`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`overview analytics failed: ${response.status}`);
    }
    overviewAnalyticsPayload = (await response.json()) as OverviewAnalytics;
    overviewAnalyticsKey = key;
  } catch {
    overviewAnalyticsPayload = { enabled: false };
    overviewAnalyticsKey = key;
  } finally {
    overviewAnalyticsLoading = false;
    renderOverviewAnalytics();
    createIcons({ icons: iconSet });
  }
}

async function fetchNodeAnalytics(route: Route) {
  if (route.kind !== "node") {
    return;
  }
  const key = `${route.nodeId}:${nodeRange}`;
  if (nodeAnalyticsLoading || nodeAnalyticsKey === key) {
    return;
  }
  if (nodeAnalyticsKey !== key) {
    nodeAnalyticsPayload = null;
  }
  nodeAnalyticsLoading = true;
  renderNodeHistory(route);
  try {
    const response = await fetch(`/api/analytics/node/${encodeURIComponent(route.nodeId)}?range=${encodeURIComponent(nodeRange)}`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`node analytics failed: ${response.status}`);
    }
    nodeAnalyticsPayload = (await response.json()) as NodeAnalytics;
    nodeAnalyticsKey = key;
  } catch {
    nodeAnalyticsPayload = { enabled: false };
    nodeAnalyticsKey = key;
  } finally {
    nodeAnalyticsLoading = false;
    renderNodeHistory(route);
    createIcons({ icons: iconSet });
  }
}

function render(snapshot: ClusterSnapshot) {
  syncRefreshControl(clusterRefreshInterval(snapshot));
  const route = currentRoute();
  const selectedNode = route.kind === "node" ? findNode(snapshot, route.nodeId) : null;
  renderNav(snapshot, route);
  renderHeader(snapshot, route, selectedNode);
  if (route.kind === "overview") {
    summaryGrid.hidden = false;
    overviewAnalytics.hidden = false;
    fabricBand.hidden = false;
    gpuGrid.hidden = true;
    nodeHistorySection.hidden = true;
    processSection.hidden = true;
    renderSummary(snapshot);
    renderOverviewAnalytics();
    renderFabric(snapshot);
    fetchOverviewAnalytics();
  } else {
    summaryGrid.hidden = false;
    overviewAnalytics.hidden = true;
    fabricBand.hidden = true;
    gpuGrid.hidden = false;
    nodeHistorySection.hidden = false;
    processSection.hidden = false;
    renderNodeSummary(route.nodeId, selectedNode);
    renderGpuGrid(route.nodeId, selectedNode);
    renderNodeHistory(route);
    fetchNodeAnalytics(route);
    renderProcesses(route.nodeId, selectedNode);
  }
  createIcons({ icons: iconSet });
}

function renderRefreshControl(intervals: number[], selected: number | null) {
  const values = intervals.filter((interval) => Number.isFinite(interval) && interval > 0);
  refreshControl.innerHTML = values
    .map(
      (interval) => `
        <button
          class="refresh-option"
          type="button"
          data-refresh-interval="${interval}"
          aria-pressed="false"
        >${formatInterval(interval)}</button>
      `,
    )
    .join("");
  syncRefreshControl(selected);
}

function syncRefreshControl(interval: number | null | undefined) {
  if (typeof interval === "number" && Number.isFinite(interval)) {
    currentRefreshInterval = interval;
  }
  const buttons = refreshControl.querySelectorAll<HTMLButtonElement>("[data-refresh-interval]");
  for (const button of buttons) {
    const value = Number(button.dataset.refreshInterval);
    const active = sameInterval(value, currentRefreshInterval);
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.disabled = refreshPending;
  }
}

function renderHeader(snapshot: ClusterSnapshot, route: Route, selectedNode: NodeSnapshot | null) {
  const totals = snapshot.totals;
  const latency = maxClusterLatency(snapshot);
  const latencyText = latency === null ? "latency n/a" : `${latency.toFixed(0)} ms max`;
  if (route.kind === "node") {
    nodeLine.textContent = selectedNode
      ? `${selectedNode.node_id} · ${selectedNode.status} · ${selectedNode.totals.gpu_count} GPUs · ${fmtLatency(selectedNode)} · seq ${selectedNode.seq}`
      : `${route.nodeId} · node not found · ${totals.node_count} nodes`;
  } else {
    nodeLine.textContent = `${totals.node_count} nodes · ${totals.online_node_count} online · ${totals.gpu_count} GPUs · ${latencyText} · seq ${snapshot.seq}`;
  }
  setLiveState(paused ? "paused" : snapshot.ok ? "live" : totals.node_count ? "error" : "connecting");
}

function renderNav(snapshot: ClusterSnapshot | null, route: Route) {
  const overviewActive = route.kind === "overview";
  const nodeLinks = (snapshot?.nodes || [])
    .map((node) => {
      const active =
        route.kind === "node" && (route.nodeId === node.node_id || route.nodeId === node.hostname);
      return `
        <a class="nav-link ${active ? "is-active" : ""}" ${active ? `aria-current="page"` : ""} href="/nodes/${encodeURIComponent(node.node_id)}">
          <i data-lucide="server"></i>
          <span>${escapeHtml(node.node_id)}</span>
        </a>
      `;
    })
    .join("");
  topNav.innerHTML = `
    <a class="nav-link ${overviewActive ? "is-active" : ""}" ${overviewActive ? `aria-current="page"` : ""} href="/overview">
      <i data-lucide="list-tree"></i>
      <span>Overview</span>
    </a>
    ${nodeLinks}
  `;
}

function renderSummary(snapshot: ClusterSnapshot) {
  const totals = snapshot.totals;
  summaryGrid.innerHTML = [
    metricCard("server", "Nodes", `${totals.online_node_count} / ${totals.node_count}`, `${totals.stale_node_count} stale · ${totals.offline_node_count} offline`, nodeHealthPercent(totals), "green"),
    metricCard("activity", "GPU Avg", fmtPct(totals.avg_gpu_utilization), `${totals.gpu_count} GPUs`, totals.avg_gpu_utilization, "cyan"),
    metricCard("database", "Memory Used", `${fmtGiB(totals.memory_used_mb)} / ${fmtGiB(totals.memory_total_mb)}`, fmtPct(totals.avg_memory_utilization), totals.avg_memory_utilization, "violet"),
    metricCard("zap", "Power", `${totals.power_watts.toFixed(0)} W / ${totals.power_limit_watts.toFixed(0)} W`, totals.power_limit_watts ? fmtPct((totals.power_watts / totals.power_limit_watts) * 100) : "n/a", totals.power_limit_watts ? (totals.power_watts / totals.power_limit_watts) * 100 : 0, "amber"),
    metricCard("users", "Tasks", `${totals.active_processes}`, `max ${totals.max_temperature_c}°C`, Math.min(100, (totals.active_processes / Math.max(1, totals.gpu_count * 4)) * 100), "red"),
  ].join("");
}

function renderNodeSummary(nodeId: string, node: NodeSnapshot | null) {
  if (!node) {
    summaryGrid.innerHTML = [
      metricCard("server", "Node", nodeId, "not found", 0, "red"),
      metricCard("activity", "GPU Avg", "n/a", "0 GPUs", 0, "cyan"),
      metricCard("database", "Memory Used", "n/a", "n/a", 0, "violet"),
      metricCard("zap", "Power", "n/a", "n/a", 0, "amber"),
      metricCard("users", "Tasks", "0", "no active tasks", 0, "red"),
    ].join("");
    return;
  }

  const totals = node.totals;
  summaryGrid.innerHTML = [
    metricCard("server", "Node", node.node_id, `${node.status} · ${node.hostname}`, node.status === "online" ? 100 : 0, node.status === "online" ? "green" : "red"),
    metricCard("activity", "GPU Avg", fmtPct(totals.avg_gpu_utilization), `${totals.gpu_count} GPUs`, totals.avg_gpu_utilization, "cyan"),
    metricCard("database", "Memory Used", `${fmtGiB(totals.memory_used_mb)} / ${fmtGiB(totals.memory_total_mb)}`, fmtPct(totals.avg_memory_utilization), totals.avg_memory_utilization, "violet"),
    metricCard("zap", "Power", `${totals.power_watts.toFixed(0)} W / ${totals.power_limit_watts.toFixed(0)} W`, totals.power_limit_watts ? fmtPct((totals.power_watts / totals.power_limit_watts) * 100) : "n/a", totals.power_limit_watts ? (totals.power_watts / totals.power_limit_watts) * 100 : 0, "amber"),
    metricCard("users", "Tasks", `${totals.active_processes}`, `max ${totals.max_temperature_c}°C`, Math.min(100, (totals.active_processes / Math.max(1, totals.gpu_count * 4)) * 100), "red"),
  ].join("");
}

function renderFabric(snapshot: ClusterSnapshot) {
  const configItems = fabricConfigItems(snapshot);
  const nodeCards = snapshot.nodes
    .map(
      (node) => `
        <a
          class="fabric-node-card is-${escapeAttr(node.status)} ${fabricNodeSizeClass(node)}"
          href="/nodes/${encodeURIComponent(node.node_id)}"
          title="${escapeAttr(node.error || node.hostname)}"
        >
          <div class="fabric-node-head">
            <div>
              <span>${escapeHtml(node.node_id)}</span>
              <strong>${escapeHtml(node.hostname)}</strong>
            </div>
            <em>${escapeHtml(node.status)}</em>
          </div>
          <div class="fabric-node-meta">
            ${node.totals.gpu_count} GPUs · ${fmtPct(node.totals.avg_gpu_utilization)} avg · ${fmtLatency(node)}
          </div>
          <div class="fabric-node-gpus">
            ${node.gpus.map((gpu) => fabricGpuChip(node, gpu)).join("") || `<span class="fabric-empty">no GPUs</span>`}
          </div>
        </a>
      `,
    )
    .join("");
  fabricBand.innerHTML = `
    <div class="fabric-copy">
      <div class="fabric-config">
        <div class="fabric-title">
          <span class="fabric-kicker">Cluster fabric</span>
          <strong>${escapeHtml(fabricConfigSummary(snapshot, configItems))}</strong>
        </div>
        <div class="fabric-config-chips">
          ${renderFabricConfigChips(configItems)}
        </div>
      </div>
      <div class="fabric-stats">
        <span>${snapshot.totals.online_node_count}/${snapshot.totals.node_count} online</span>
        <span>${snapshot.totals.gpu_count} GPUs</span>
        <span>${fmtGiB(snapshot.totals.memory_total_mb)} Memory total</span>
      </div>
    </div>
    <div class="fabric-node-grid">${nodeCards || `<div class="empty-panel">no nodes</div>`}</div>
  `;
}

function renderOverviewAnalytics() {
  const payload = overviewAnalyticsPayload;
  const disabled = payload && payload.enabled === false;
  overviewAnalytics.innerHTML = `
    <div class="analytics-head">
      <div>
        <span class="section-kicker">Historical analytics</span>
        <h2>Usage, jobs, and low-utilization signals</h2>
        <p>${disabled ? "SQLite history is not enabled" : overviewMetaText(payload)}</p>
      </div>
      <div class="analytics-actions">
        ${rangeButtons(OVERVIEW_ANALYTICS_RANGES, overviewRange, "overview-range")}
        <button class="icon-button" type="button" data-analytics-action="overview-refresh" aria-label="Refresh analytics" title="Refresh analytics">
          <i data-lucide="refresh-cw"></i>
        </button>
      </div>
    </div>
    ${
      disabled
        ? disabledAnalytics("Enable DB_PATH to show historical usage without changing the realtime path.")
        : overviewAnalyticsLoading && !payload
          ? loadingPanel("loading historical usage")
          : overviewAnalyticsBody(payload)
    }
  `;
}

function overviewAnalyticsBody(payload: OverviewAnalytics | null) {
  const users = payload?.user_gpu_hours || [];
  const jobs = payload?.job_rankings || [];
  const anomalies = payload?.anomalies || [];
  const offHours = payload?.off_hours;
  return `
    <div class="analytics-grid">
      <article class="analytics-card span-6">
        <div class="card-title">
          <span><i data-lucide="bar-chart-3"></i>User GPU hours</span>
          <em>weighted sort</em>
        </div>
        ${users.length ? analyticsTable(
          ["User", "GPU h", "Weighted", "Tasks", "Models"],
          users.slice(0, 8).map((item) => [
            item.user,
            fmtNumber(item.gpu_hours),
            fmtNumber(item.weighted_gpu_hours),
            `${item.task_count} / ${item.job_count}`,
            item.top_gpu_models.map((model) => model.name).join(", ") || "n/a",
          ]),
        ) : emptyInline("no user history in this range")}
      </article>
      <article class="analytics-card span-6">
        <div class="card-title">
          <span><i data-lucide="table-2"></i>Job rankings</span>
          <em>top 8</em>
        </div>
        ${jobs.length ? analyticsTable(
          ["Task", "User", "Node", "GPU h", "Runtime"],
          jobs.slice(0, 8).map((item) => [
            item.task_name,
            item.user,
            item.node_id,
            fmtNumber(item.weighted_gpu_hours),
            fmtDuration(item.duration_seconds),
          ]),
        ) : emptyInline("no job history in this range")}
      </article>
      <article class="analytics-card span-8">
        <div class="card-title">
          <span><i data-lucide="alert-triangle"></i>Low-utilization reservations</span>
          <em>${anomalies.length} signals</em>
        </div>
        <div class="anomaly-list">
          ${anomalies.length ? anomalies.slice(0, 6).map(anomalyCard).join("") : emptyInline("no clear low-utilization reservations")}
        </div>
      </article>
      <article class="analytics-card span-4">
        <div class="card-title">
          <span><i data-lucide="moon"></i>Off-hour activity</span>
          <em>Beijing time</em>
        </div>
        ${offHoursCard(offHours)}
      </article>
    </div>
  `;
}

function anomalyCard(item: AnomalyItem) {
  return `
    <div class="anomaly-card">
      <strong>${escapeHtml(item.task_name)}</strong>
      <span>${escapeHtml(item.user)} · ${escapeHtml(item.node_id)} · ${fmtDuration(item.duration_seconds)}</span>
      <div>
        <b>${fmtNumber(item.gpu_memory_gb)} GiB</b>
        <b>${fmtPct(item.recent_avg_gpu_utilization)} recent GPU</b>
        <b>${fmtDuration(item.idle_tail_seconds)} idle tail</b>
      </div>
    </div>
  `;
}

function offHoursCard(item: OffHours | undefined) {
  if (!item) {
    return emptyInline("no off-hour data");
  }
  return `
    <div class="offhour-grid">
      <span><b>${item.night_job_count}</b><small>night jobs</small></span>
      <span><b>${item.weekend_job_count}</b><small>weekend jobs</small></span>
      <span><b>${fmtNumber(item.night_gpu_hours)}</b><small>night GPU h</small></span>
      <span><b>${fmtNumber(item.weekend_gpu_hours)}</b><small>weekend GPU h</small></span>
    </div>
    <div class="top-users">
      ${(item.top_users || []).map((user) => `<span>${escapeHtml(user.user)} <b>${user.job_count}</b></span>`).join("") || `<span>no off-hour users</span>`}
    </div>
  `;
}

function renderNodeHistory(route: Route) {
  if (route.kind !== "node") {
    nodeHistorySection.innerHTML = "";
    return;
  }
  const payload = nodeAnalyticsPayload;
  const disabled = payload && payload.enabled === false;
  nodeHistorySection.innerHTML = `
    <div class="analytics-head">
      <div>
        <span class="section-kicker">Node history</span>
        <h2>${escapeHtml(route.nodeId)} trends and heatmap</h2>
        <p>${disabled ? "SQLite history is not enabled" : overviewMetaText(payload)}</p>
      </div>
      <div class="analytics-actions">
        ${rangeButtons(NODE_ANALYTICS_RANGES, nodeRange, "node-range")}
        ${metricButtons()}
        <button class="icon-button" type="button" data-analytics-action="node-refresh" aria-label="Refresh history" title="Refresh history">
          <i data-lucide="refresh-cw"></i>
        </button>
      </div>
    </div>
    ${
      disabled
        ? disabledAnalytics("Enable DB_PATH to show node rollups and heatmaps.")
        : nodeAnalyticsLoading && !payload
          ? loadingPanel("loading node history")
          : nodeHistoryBody(payload)
    }
  `;
}

function nodeHistoryBody(payload: NodeAnalytics | null) {
  const series = payload?.series || [];
  const heatmap = payload?.heatmap || [];
  return `
    <div class="analytics-grid">
      <article class="analytics-card span-8">
        <div class="card-title">
          <span><i data-lucide="line-chart"></i>${metricLabel(nodeMetric)} history</span>
          <em>${payload?.bucket_seconds ? `${formatBucket(payload.bucket_seconds)} buckets` : "rollup"}</em>
        </div>
        ${series.some((item) => item.points.length) ? lineChart(series, nodeMetric) : emptyInline("no rollup points in this range")}
      </article>
      <article class="analytics-card span-4">
        <div class="card-title">
          <span><i data-lucide="activity"></i>GPU heatmap</span>
          <em>${payload?.heatmap_bucket_seconds ? formatBucket(payload.heatmap_bucket_seconds) : "bucketed"}</em>
        </div>
        ${heatmap.some((item) => item.buckets.length) ? heatmapChart(heatmap) : emptyInline("no heatmap buckets in this range")}
      </article>
    </div>
  `;
}

function fabricNodeSizeClass(node: NodeSnapshot) {
  const gpuCount = node.totals.gpu_count;
  if (gpuCount >= 4) {
    return "is-node-span-4";
  }
  if (gpuCount >= 3) {
    return "is-node-span-3";
  }
  return "is-node-span-2";
}

function fabricGpuChip(node: NodeSnapshot, gpu: GpuInfo) {
  return `
    <div class="fabric-chip ${statusClass(gpu.utilization_gpu)}" title="${escapeAttr(node.node_id)} GPU${gpu.index}">
      <span>GPU${gpu.index}</span>
      <strong>${Math.round(gpu.utilization_gpu)}%</strong>
      <small>${fmtGiB(gpu.memory_used_mb)}</small>
    </div>
  `;
}

function renderGpuGrid(nodeId: string, node: NodeSnapshot | null) {
  if (!node) {
    gpuGrid.innerHTML = `<div class="empty-panel">Node ${escapeHtml(nodeId)} not found</div>`;
    return;
  }
  const items = node.gpus.map((gpu) => ({ node, gpu }));
  if (!items.length) {
    gpuGrid.innerHTML = `<div class="empty-panel">${escapeHtml(node.error || "No GPU snapshot available")}</div>`;
    return;
  }
  gpuGrid.innerHTML = items
    .map(({ node, gpu }) => gpuCard(node, gpu, node.history[gpu.gpu_id || `${node.node_id}:${gpu.uuid}`] || {}))
    .join("");
}

function gpuCard(node: NodeSnapshot, gpu: GpuInfo, history: Record<string, number[]>) {
  const subtitle = [
    node.node_id,
    gpu.pstate,
    gpu.compute_mode,
    gpu.mig_mode ? `MIG ${gpu.mig_mode}` : null,
    gpu.ecc_mode ? `ECC ${gpu.ecc_mode}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const clock = [
    gpu.clock_sm_mhz ? `SM ${gpu.clock_sm_mhz} MHz` : null,
    gpu.clock_mem_mhz ? `MEM ${gpu.clock_mem_mhz} MHz` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return `
    <article class="gpu-card">
      <div class="gpu-head">
        <div>
          <span class="gpu-index">${escapeHtml(node.node_id)} · GPU ${gpu.index}</span>
          <h3>${escapeHtml(compactGpuName(gpu.name))}</h3>
          <p>${escapeHtml(subtitle || gpu.uuid)}</p>
        </div>
        <div class="temp-badge ${tempClass(gpu.temperature_c)}">${gpu.temperature_c}°C</div>
      </div>

      <div class="spark-wrap">
        ${sparkline(history.gpu || [], "var(--green)", 100)}
      </div>

      <div class="bar-stack">
        ${bar("GPU", gpu.utilization_gpu, fmtPct(gpu.utilization_gpu), "green")}
        ${bar("Memory", gpu.memory_percent, `${fmtGiB(gpu.memory_used_mb)} / ${fmtGiB(gpu.memory_total_mb)}`, "cyan")}
        ${bar("Power", gpu.power_percent, `${gpu.power_watts.toFixed(0)} / ${gpu.power_limit_watts.toFixed(0)} W`, "amber")}
      </div>

      <div class="mini-stats">
        <span><i data-lucide="gauge"></i>${fmtPct(gpu.utilization_mem)} mem util</span>
        <span><i data-lucide="clock-3"></i>${escapeHtml(clock || "clock n/a")}</span>
        <span><i data-lucide="server"></i>${escapeHtml(node.status)} · ${fmtLatency(node)}</span>
        <span><i data-lucide="cpu"></i>${escapeHtml(gpu.pci_bus_id || gpu.uuid)}</span>
      </div>
    </article>
  `;
}

function renderProcesses(nodeId: string, node: NodeSnapshot | null) {
  type Row = {
    node: string;
    gpu: number;
    user: string;
    pid: string;
    task: string;
    memory: number;
    runtime: number | null;
    kind: string;
    title: string;
  };

  const rows: Row[] = [];
  if (node) {
    for (const gpu of node.gpus) {
      for (const process of gpu.processes || []) {
        rows.push({
          node: node.node_id,
          gpu: gpu.index,
          user: process.user || "unknown",
          pid: String(process.pid),
          task: process.task_name || process.name,
          memory: process.gpu_memory_mb,
          runtime: process.runtime_seconds ?? null,
          kind: process.kind,
          title: process.cmdline || process.exe || process.name,
        });
      }
      for (const other of gpu.other_users || []) {
        rows.push({
          node: node.node_id,
          gpu: gpu.index,
          user: other.user,
          pid: `${other.process_count} procs`,
          task: "aggregate workload",
          memory: other.total_memory_mb,
          runtime: other.runtime_seconds ?? null,
          kind: "aggregate",
          title: `${other.process_count} processes`,
        });
      }
    }
  }

  rows.sort((a, b) => a.node.localeCompare(b.node) || a.gpu - b.gpu || b.memory - a.memory || (b.runtime || 0) - (a.runtime || 0));
  processMeta.textContent = `${node?.node_id || nodeId} · ${rows.length} active`;
  if (!rows.length) {
    processRows.innerHTML = `<tr><td colspan="8" class="empty">no active GPU tasks</td></tr>`;
    return;
  }

  processRows.innerHTML = rows
    .slice(0, 80)
    .map(
      (row) => `
      <tr title="${escapeAttr(row.title)}">
        <td>${escapeHtml(row.node)}</td>
        <td><span class="gpu-pill">GPU${row.gpu}</span></td>
        <td>${escapeHtml(row.user)}</td>
        <td>${escapeHtml(row.pid)}</td>
        <td>${escapeHtml(row.task)}</td>
        <td>${fmtGiB(row.memory)}</td>
        <td>${fmtDuration(row.runtime)}</td>
        <td>${escapeHtml(row.kind)}</td>
      </tr>
    `,
    )
    .join("");
}

function metricCard(iconName: string, label: string, value: string, meta: string, percent: number, tone: string) {
  return `
    <article class="metric-card tone-${tone}">
      <div class="metric-icon">${icon(iconName)}</div>
      <div>
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
        <small>${escapeHtml(meta)}</small>
      </div>
      <div class="metric-rail"><span style="width:${clamp(percent)}%"></span></div>
    </article>
  `;
}

function bar(label: string, value: number, meta: string, tone: string) {
  return `
    <div class="bar-row tone-${tone}">
      <div class="bar-label">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(meta)}</strong>
      </div>
      <div class="bar-track"><span style="width:${clamp(value)}%"></span></div>
    </div>
  `;
}

function sparkline(values: number[], color: string, max: number) {
  const width = 180;
  const height = 46;
  if (values.length < 2) {
    return `<svg class="spark" viewBox="0 0 ${width} ${height}" role="img" aria-label="GPU history"></svg>`;
  }
  const points = values
    .map((value, index) => {
      const x = (index / Math.max(1, values.length - 1)) * width;
      const y = height - (clamp(value) / max) * (height - 6) - 3;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return `
    <svg class="spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="GPU history">
      <polyline points="${points}" style="stroke:${color}"></polyline>
    </svg>
  `;
}

function rangeButtons(values: string[], selected: string, action: string) {
  return `
    <div class="segmented" role="group">
      ${values
        .map(
          (value) => `
            <button
              class="${value === selected ? "is-active" : ""}"
              type="button"
              data-analytics-action="${action}"
              data-range="${value}"
              aria-pressed="${value === selected ? "true" : "false"}"
            >${value}</button>
          `,
        )
        .join("")}
    </div>
  `;
}

function metricButtons() {
  return `
    <div class="segmented metric-tabs" role="group">
      ${NODE_METRICS.map(
        (metric) => `
          <button
            class="${metric.key === nodeMetric ? "is-active" : ""}"
            type="button"
            data-analytics-action="node-metric"
            data-metric="${metric.key}"
            aria-pressed="${metric.key === nodeMetric ? "true" : "false"}"
          >${metric.label}</button>
        `,
      ).join("")}
    </div>
  `;
}

function analyticsTable(headers: string[], rows: string[][]) {
  return `
    <div class="analytics-table-wrap">
      <table class="analytics-table">
        <thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr></thead>
        <tbody>
          ${rows
            .map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`)
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function lineChart(series: AnalyticsSeries[], metric: NodeMetric) {
  const width = 760;
  const height = 260;
  const pad = { left: 42, right: 14, top: 16, bottom: 28 };
  const points = series.flatMap((item) => item.points);
  const minTime = Math.min(...points.map((point) => point.bucket_start));
  const maxTime = Math.max(...points.map((point) => point.bucket_start));
  const metricDef = NODE_METRICS.find((item) => item.key === metric) || NODE_METRICS[0];
  const maxValue = metricDef.max || Math.max(1, ...points.map((point) => metricValue(point, metric))) * 1.08;
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const pathFor = (item: AnalyticsSeries) =>
    item.points
      .map((point) => {
        const x = pad.left + ((point.bucket_start - minTime) / Math.max(1, maxTime - minTime)) * plotWidth;
        const y = pad.top + plotHeight - (metricValue(point, metric) / maxValue) * plotHeight;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  return `
    <div class="chart-wrap">
      <svg class="line-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${escapeAttr(metricDef.label)} history">
        <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}"></line>
        <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}"></line>
        <text x="4" y="${pad.top + 8}">${escapeHtml(metricTick(maxValue, metric))}</text>
        <text x="${pad.left}" y="${height - 7}">${escapeHtml(formatTime(minTime))}</text>
        <text x="${width - pad.right - 92}" y="${height - 7}">${escapeHtml(formatTime(maxTime))}</text>
        ${series
          .map((item, index) =>
            item.points.length
              ? `<polyline points="${pathFor(item)}" style="stroke:${CHART_COLORS[index % CHART_COLORS.length]}"><title>GPU${item.gpu_index ?? "?"} ${escapeHtml(item.gpu_name || item.gpu_uuid)}</title></polyline>`
              : "",
          )
          .join("")}
      </svg>
      <div class="chart-legend">
        ${series
          .map(
            (item, index) => `
              <span><b style="background:${CHART_COLORS[index % CHART_COLORS.length]}"></b>GPU${item.gpu_index ?? "?"}</span>
            `,
          )
          .join("")}
      </div>
    </div>
  `;
}

function heatmapChart(items: AnalyticsHeatmap[]) {
  const allStarts = Array.from(new Set(items.flatMap((item) => item.buckets.map((bucket) => bucket.bucket_start)))).sort((a, b) => a - b);
  const columns = Math.max(1, allStarts.length);
  const indexByStart = new Map(allStarts.map((value, index) => [value, index]));
  return `
    <div class="heatmap" style="--heat-cols:${columns}">
      ${items
        .map((item) => {
          const buckets = new Map(item.buckets.map((bucket) => [bucket.bucket_start, bucket]));
          return `
            <div class="heat-row-label">GPU${item.gpu_index ?? "?"}</div>
            <div class="heat-row">
              ${allStarts
                .map((start) => {
                  const bucket = buckets.get(start);
                  const value = bucket?.avg_gpu_utilization || 0;
                  return `<span class="${heatClass(value)}" title="${escapeAttr(`GPU${item.gpu_index ?? "?"} · ${formatTime(start)} · ${fmtPct(value)} avg · ${fmtGiB(bucket?.avg_memory_used_mb || 0)}`)}" style="grid-column:${(indexByStart.get(start) || 0) + 1}"></span>`;
                })
                .join("")}
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function metricValue(point: AnalyticsPoint, metric: NodeMetric) {
  return Number(point[metric] || 0);
}

function metricLabel(metric: NodeMetric) {
  return NODE_METRICS.find((item) => item.key === metric)?.label || "GPU";
}

function metricTick(value: number, metric: NodeMetric) {
  if (metric === "avg_memory_used_mb") {
    return fmtGiB(value);
  }
  if (metric === "avg_gpu_utilization") {
    return fmtPct(value);
  }
  if (metric === "avg_power_watts") {
    return `${value.toFixed(0)} W`;
  }
  return `${value.toFixed(0)} C`;
}

function heatClass(value: number) {
  if (value >= 70) return "heat hot";
  if (value >= 30) return "heat active";
  if (value >= 5) return "heat low";
  return "heat idle";
}

function overviewMetaText(payload: AnalyticsMeta | null) {
  if (!payload?.generated_at) {
    return "waiting for SQLite rollups";
  }
  const range = payload.range_start && payload.range_end ? `${formatTime(payload.range_start)} - ${formatTime(payload.range_end)}` : "selected range";
  return `${range} · generated ${formatTime(payload.generated_at)} · ${payload.timezone || "Asia/Shanghai"}`;
}

function disabledAnalytics(message: string) {
  return `<div class="empty-panel analytics-disabled">${escapeHtml(message)}</div>`;
}

function loadingPanel(message: string) {
  return `<div class="empty-panel">${escapeHtml(message)}</div>`;
}

function emptyInline(message: string) {
  return `<div class="empty-inline">${escapeHtml(message)}</div>`;
}

function flattenGpus(snapshot: ClusterSnapshot) {
  return snapshot.nodes.flatMap((node) => node.gpus.map((gpu) => ({ node, gpu })));
}

function findNode(snapshot: ClusterSnapshot, nodeId: string) {
  return snapshot.nodes.find((node) => node.node_id === nodeId || node.hostname === nodeId) || null;
}

function clusterRefreshInterval(snapshot: ClusterSnapshot | null) {
  return snapshot?.nodes.find((node) => node.status === "online")?.refresh_interval ?? snapshot?.nodes[0]?.refresh_interval ?? null;
}

function nodeHealthPercent(totals: ClusterSnapshot["totals"]) {
  if (!totals.node_count) {
    return 0;
  }
  return (totals.online_node_count / totals.node_count) * 100;
}

function maxClusterLatency(snapshot: ClusterSnapshot) {
  const values = snapshot.nodes
    .map((node) => (node.received_at && node.sampled_at ? (node.received_at - node.sampled_at) * 1000 : null))
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  return values.length ? Math.max(...values) : null;
}

function fmtLatency(node: NodeSnapshot) {
  if (!node.received_at || !node.sampled_at) {
    return "latency n/a";
  }
  return `${Math.max(0, (node.received_at - node.sampled_at) * 1000).toFixed(0)} ms`;
}

function fabricConfigSummary(snapshot: ClusterSnapshot, items: FabricConfigItem[]) {
  if (!snapshot.nodes.length) {
    return "No nodes connected";
  }
  if (!items.length) {
    return `${snapshot.nodes.length} nodes`;
  }
  const architectureCount = new Set(items.map((item) => item.architecture).filter(Boolean)).size;
  const parts = [
    `${snapshot.totals.gpu_count} GPUs`,
    `${items.length} GPU ${items.length === 1 ? "type" : "types"}`,
  ];
  if (architectureCount) {
    parts.push(`${architectureCount} ${architectureCount === 1 ? "architecture" : "architectures"}`);
  }
  return parts.join(" · ");
}

type FabricConfigItem = {
  count: number;
  name: string;
  architecture: string | null;
};

function fabricConfigItems(snapshot: ClusterSnapshot): FabricConfigItem[] {
  const source = snapshot.nodes.flatMap((node) => {
    if (node.hardware?.gpus.length) {
      return node.hardware.gpus.map((gpu) => ({ name: gpu.name, architecture: gpu.architecture || null }));
    }
    return node.gpus.map((gpu) => ({ name: gpu.name, architecture: null }));
  });
  const configs = new Map<string, FabricConfigItem>();
  for (const gpu of source) {
    const name = compactGpuName(gpu.name);
    const key = `${name}\u0000${gpu.architecture || ""}`;
    const config = configs.get(key);
    if (config) {
      config.count += 1;
    } else {
      configs.set(key, { count: 1, name, architecture: gpu.architecture });
    }
  }
  return Array.from(configs.values()).sort((left, right) => right.count - left.count || left.name.localeCompare(right.name));
}

function renderFabricConfigChips(items: FabricConfigItem[]) {
  if (!items.length) {
    return `<span class="fabric-config-empty">waiting for GPU inventory</span>`;
  }
  return items
    .map(
      (item) => `
        <span class="fabric-config-chip">
          <b>${item.count} ×</b>
          <span>
            <strong>${escapeHtml(item.name)}</strong>
            ${item.architecture ? `<small>${escapeHtml(item.architecture)}</small>` : ""}
          </span>
        </span>
      `,
    )
    .join("");
}

function setLiveState(state: "connecting" | "live" | "paused" | "offline" | "error") {
  liveState.className = `live-pill is-${state}`;
  liveState.innerHTML = `<span></span>${state}`;
}

function icon(name: string) {
  return `<i data-lucide="${name}"></i>`;
}

function compactGpuName(name: string) {
  return name.replace(/^NVIDIA\s+/, "");
}

function sameInterval(left: number | null, right: number | null) {
  if (left === null || right === null) {
    return false;
  }
  return Math.abs(left - right) < 1e-9;
}

function formatInterval(seconds: number) {
  return seconds < 1 ? `${seconds.toFixed(1)}s` : `${seconds.toFixed(0)}s`;
}

function fmtGiB(mib: number) {
  if (!Number.isFinite(mib)) {
    return "n/a";
  }
  return `${(mib / 1024).toFixed(mib >= 10240 ? 1 : 2)} GiB`;
}

function fmtPct(value: number) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return `${value.toFixed(value % 1 ? 1 : 0)}%`;
}

function fmtNumber(value: number) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: value >= 10 ? 1 : 2 });
}

function formatBucket(seconds: number) {
  if (seconds < 60) {
    return `${seconds}s`;
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)}m`;
  }
  return `${Math.round(seconds / 3600)}h`;
}

function formatTime(epochSeconds: number) {
  if (!Number.isFinite(epochSeconds)) {
    return "n/a";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(epochSeconds * 1000));
}

function fmtDuration(seconds: number | null) {
  if (seconds === null || !Number.isFinite(seconds)) {
    return "n/a";
  }
  if (seconds < 60) {
    return `${Math.max(0, Math.floor(seconds))}s`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ${Math.floor(seconds % 60)}s`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ${minutes % 60}m`;
  }
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

function clamp(value: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(100, value));
}

function statusClass(value: number) {
  if (value >= 80) return "is-hot";
  if (value >= 35) return "is-active";
  return "is-idle";
}

function tempClass(value: number) {
  if (value >= 80) return "is-hot";
  if (value >= 65) return "is-warm";
  return "is-cool";
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => {
    const map: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    };
    return map[char] || char;
  });
}

function escapeAttr(value: string) {
  return escapeHtml(value).replace(/\n/g, " ");
}
