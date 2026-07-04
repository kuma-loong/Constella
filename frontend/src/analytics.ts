import {
  escapeAttr,
  escapeHtml,
  fmtDuration,
  fmtGiB,
  fmtNumber,
  fmtPct,
  formatBucket,
  formatTime,
} from "./format";

export type Route = { kind: "overview" } | { kind: "node"; nodeId: string };

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
  buckets: Pick<
    AnalyticsPoint,
    "bucket_start" | "avg_gpu_utilization" | "max_gpu_utilization" | "avg_memory_used_mb" | "sample_count"
  >[];
};

type NodeMetric =
  | "avg_gpu_utilization"
  | "avg_memory_used_mb"
  | "avg_power_watts"
  | "avg_temperature_c";

type AnalyticsControllerOptions = {
  overviewElement: HTMLElement;
  nodeElement: HTMLElement;
  currentRoute: () => Route;
  renderIcons: () => void;
};

const OVERVIEW_RANGES = ["24h", "7d", "30d"];
const NODE_RANGES = ["1h", "24h", "7d", "30d"];
const NODE_METRICS: { key: NodeMetric; label: string; max?: number }[] = [
  { key: "avg_gpu_utilization", label: "GPU", max: 100 },
  { key: "avg_memory_used_mb", label: "Memory" },
  { key: "avg_power_watts", label: "Power" },
  { key: "avg_temperature_c", label: "Temp", max: 100 },
];
const CHART_COLORS = [
  "#1f9d72",
  "#2563eb",
  "#c2410c",
  "#7c3aed",
  "#be123c",
  "#0f766e",
  "#b45309",
  "#4f46e5",
];

export function createAnalyticsController({
  overviewElement,
  nodeElement,
  currentRoute,
  renderIcons,
}: AnalyticsControllerOptions) {
  let overviewRange = "7d";
  let overviewPayload: OverviewAnalytics | null = null;
  let overviewKey = "";
  let overviewLoading = false;
  let nodeRange = "24h";
  let nodeMetric: NodeMetric = "avg_gpu_utilization";
  let selectedGpuUuid: string | null = null;
  let nodePayload: NodeAnalytics | null = null;
  let nodeKey = "";
  let nodeLoading = false;

  function handleClick(target: HTMLButtonElement) {
    const action = target.dataset.analyticsAction;
    if (action === "overview-range" && target.dataset.range) {
      overviewRange = target.dataset.range;
      overviewPayload = null;
      overviewKey = "";
      renderOverview();
      void fetchOverview();
      return true;
    }
    if (action === "node-range" && target.dataset.range) {
      nodeRange = target.dataset.range;
      nodePayload = null;
      nodeKey = "";
      renderNode(currentRoute());
      void fetchNode(currentRoute());
      return true;
    }
    if (action === "node-metric" && target.dataset.metric) {
      nodeMetric = target.dataset.metric as NodeMetric;
      renderNode(currentRoute());
      return true;
    }
    if (action === "node-gpu") {
      selectedGpuUuid = target.dataset.gpuUuid || null;
      renderNode(currentRoute());
      return true;
    }
    if (action === "overview-refresh") {
      overviewKey = "";
      void fetchOverview();
      return true;
    }
    if (action === "node-refresh") {
      nodeKey = "";
      void fetchNode(currentRoute());
      return true;
    }
    return false;
  }

  async function fetchOverview() {
    const key = overviewRange;
    if (overviewLoading || overviewKey === key) {
      return;
    }
    overviewLoading = true;
    renderOverview();
    try {
      const response = await fetch(`/api/analytics/overview?range=${encodeURIComponent(overviewRange)}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`overview analytics failed: ${response.status}`);
      }
      overviewPayload = (await response.json()) as OverviewAnalytics;
      overviewKey = key;
    } catch {
      overviewPayload = { enabled: false };
      overviewKey = key;
    } finally {
      overviewLoading = false;
      renderOverview();
      renderIcons();
    }
  }

  async function fetchNode(route: Route) {
    if (route.kind !== "node") {
      return;
    }
    const key = `${route.nodeId}:${nodeRange}`;
    if (nodeLoading || nodeKey === key) {
      return;
    }
    if (nodeKey !== key) {
      nodePayload = null;
    }
    nodeLoading = true;
    renderNode(route);
    try {
      const response = await fetch(
        `/api/analytics/node/${encodeURIComponent(route.nodeId)}?range=${encodeURIComponent(nodeRange)}`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        throw new Error(`node analytics failed: ${response.status}`);
      }
      nodePayload = (await response.json()) as NodeAnalytics;
      nodeKey = key;
    } catch {
      nodePayload = { enabled: false };
      nodeKey = key;
    } finally {
      nodeLoading = false;
      renderNode(route);
      renderIcons();
    }
  }

  function renderOverview() {
    const payload = overviewPayload;
    const disabled = payload && payload.enabled === false;
    overviewElement.innerHTML = `
      <div class="analytics-head">
        <div>
          <span class="section-kicker">Historical analytics</span>
          <h2>Usage, jobs, and low-utilization signals</h2>
          <p>${disabled ? "SQLite history is not enabled" : metaText(payload)}</p>
        </div>
        <div class="analytics-actions">
          ${rangeButtons(OVERVIEW_RANGES, overviewRange, "overview-range")}
          <button class="icon-button" type="button" data-analytics-action="overview-refresh" aria-label="Refresh analytics" title="Refresh analytics">
            <i data-lucide="refresh-cw"></i>
          </button>
        </div>
      </div>
      ${
        disabled
          ? disabledAnalytics("Enable DB_PATH to show historical usage without changing the realtime path.")
          : overviewLoading && !payload
            ? loadingPanel("loading historical usage")
            : overviewBody(payload)
      }
    `;
  }

  function renderNode(route: Route) {
    if (route.kind !== "node") {
      nodeElement.innerHTML = "";
      return;
    }
    syncSelectedGpu();
    const payload = nodePayload;
    const disabled = payload && payload.enabled === false;
    nodeElement.innerHTML = `
      <div class="analytics-head">
        <div>
          <span class="section-kicker">Node history</span>
          <h2>${escapeHtml(route.nodeId)} trends and heatmap</h2>
          <p>${disabled ? "SQLite history is not enabled" : metaText(payload)}</p>
        </div>
        <div class="analytics-actions">
          ${rangeButtons(NODE_RANGES, nodeRange, "node-range")}
          ${metricButtons(nodeMetric)}
          <button class="icon-button" type="button" data-analytics-action="node-refresh" aria-label="Refresh history" title="Refresh history">
            <i data-lucide="refresh-cw"></i>
          </button>
        </div>
      </div>
      ${
        disabled
          ? disabledAnalytics("Enable DB_PATH to show node rollups and heatmaps.")
          : nodeLoading && !payload
            ? loadingPanel("loading node history")
            : nodeBody(payload)
      }
    `;
  }

  function overviewBody(payload: OverviewAnalytics | null) {
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
          ${
            users.length
              ? analyticsTable(
                  ["User", "GPU h", "Weighted", "Tasks", "Models"],
                  users.slice(0, 8).map((item) => [
                    item.user,
                    fmtNumber(item.gpu_hours),
                    fmtNumber(item.weighted_gpu_hours),
                    `${item.task_count} / ${item.job_count}`,
                    item.top_gpu_models.map((model) => model.name).join(", ") || "n/a",
                  ]),
                )
              : emptyInline("no user history in this range")
          }
        </article>
        <article class="analytics-card span-6">
          <div class="card-title">
            <span><i data-lucide="table-2"></i>Job rankings</span>
            <em>top 8</em>
          </div>
          ${
            jobs.length
              ? analyticsTable(
                  ["Task", "User", "Node", "GPU h", "Runtime"],
                  jobs.slice(0, 8).map((item) => [
                    item.task_name,
                    item.user,
                    item.node_id,
                    fmtNumber(item.weighted_gpu_hours),
                    fmtDuration(item.duration_seconds),
                  ]),
                )
              : emptyInline("no job history in this range")
          }
        </article>
        <article class="analytics-card span-8">
          <div class="card-title">
            <span><i data-lucide="alert-triangle"></i>Low-utilization reservations</span>
            <em>${anomalies.length} signals</em>
          </div>
          <div class="anomaly-list">
            ${
              anomalies.length
                ? anomalies.slice(0, 6).map(anomalyCard).join("")
                : emptyInline("no clear low-utilization reservations")
            }
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

  function nodeBody(payload: NodeAnalytics | null) {
    const series = payload?.series || [];
    const heatmap = payload?.heatmap || [];
    const activeLabel = selectedGpuUuid
      ? `GPU${series.find((item) => item.gpu_uuid === selectedGpuUuid)?.gpu_index ?? "?"}`
      : "all GPUs";
    return `
      <div class="analytics-grid">
        <article class="analytics-card span-8">
          <div class="card-title">
            <span><i data-lucide="line-chart"></i>${metricLabel(nodeMetric)} history</span>
            <em>${activeLabel} · ${
              payload?.bucket_seconds ? `${formatBucket(payload.bucket_seconds)} buckets` : "rollup"
            }</em>
          </div>
          ${
            series.some((item) => item.points.length)
              ? lineChart(series, nodeMetric)
              : emptyInline("no rollup points in this range")
          }
        </article>
        <article class="analytics-card span-4">
          <div class="card-title">
            <span><i data-lucide="activity"></i>GPU heatmap</span>
            <em>${payload?.heatmap_bucket_seconds ? formatBucket(payload.heatmap_bucket_seconds) : "bucketed"}</em>
          </div>
          ${
            heatmap.some((item) => item.buckets.length)
              ? heatmapChart(heatmap)
              : emptyInline("no heatmap buckets in this range")
          }
        </article>
      </div>
    `;
  }

  function syncSelectedGpu() {
    const series = nodePayload?.series || [];
    if (!selectedGpuUuid || !series.length) {
      return;
    }
    if (!series.some((item) => item.gpu_uuid === selectedGpuUuid)) {
      selectedGpuUuid = null;
    }
  }

  function metricButtons(selected: NodeMetric) {
    return `
      <div class="segmented metric-tabs" role="group">
        ${NODE_METRICS.map(
          (metric) => `
            <button
              class="${metric.key === selected ? "is-active" : ""}"
              type="button"
              data-analytics-action="node-metric"
              data-metric="${metric.key}"
              aria-pressed="${metric.key === selected ? "true" : "false"}"
            >${metric.label}</button>
          `,
        ).join("")}
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
    const maxValue =
      metricDef.max || Math.max(1, ...points.map((point) => metricValue(point, metric))) * 1.08;
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
            .map((item, index) => {
              if (!item.points.length) {
                return "";
              }
              const muted = selectedGpuUuid !== null && selectedGpuUuid !== item.gpu_uuid;
              return `<polyline class="${muted ? "is-muted" : "is-selected"}" points="${pathFor(item)}" style="stroke:${CHART_COLORS[index % CHART_COLORS.length]}"><title>GPU${item.gpu_index ?? "?"} ${escapeHtml(item.gpu_name || item.gpu_uuid)}</title></polyline>`;
            })
            .join("")}
        </svg>
        <div class="chart-legend">
          <button class="${selectedGpuUuid === null ? "is-active" : ""}" type="button" data-analytics-action="node-gpu" aria-pressed="${selectedGpuUuid === null ? "true" : "false"}">
            <b style="background:var(--text)"></b>All
          </button>
          ${series
            .map(
              (item, index) => `
                <button
                  class="${selectedGpuUuid === item.gpu_uuid ? "is-active" : ""}"
                  type="button"
                  data-analytics-action="node-gpu"
                  data-gpu-uuid="${escapeAttr(item.gpu_uuid)}"
                  aria-pressed="${selectedGpuUuid === item.gpu_uuid ? "true" : "false"}"
                ><b style="background:${CHART_COLORS[index % CHART_COLORS.length]}"></b>GPU${item.gpu_index ?? "?"}</button>
              `,
            )
            .join("")}
        </div>
      </div>
    `;
  }

  return {
    handleClick,
    fetchOverview,
    fetchNode,
    renderOverview,
    renderNode,
  };
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
      ${
        (item.top_users || [])
          .map((user) => `<span>${escapeHtml(user.user)} <b>${user.job_count}</b></span>`)
          .join("") || `<span>no off-hour users</span>`
      }
    </div>
  `;
}

function heatmapChart(items: AnalyticsHeatmap[]) {
  const allStarts = Array.from(
    new Set(items.flatMap((item) => item.buckets.map((bucket) => bucket.bucket_start))),
  ).sort((a, b) => a - b);
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

function metaText(payload: AnalyticsMeta | null) {
  if (!payload?.generated_at) {
    return "waiting for SQLite rollups";
  }
  const range =
    payload.range_start && payload.range_end
      ? `${formatTime(payload.range_start)} - ${formatTime(payload.range_end)}`
      : "selected range";
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
