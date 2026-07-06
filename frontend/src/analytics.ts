import {
  escapeAttr,
  escapeHtml,
  fmtDuration,
  fmtGiB,
  fmtNumber,
  fmtPct,
  DISPLAY_TIME_ZONE,
  formatBucket,
  formatTime,
} from "./format";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

export type Route = { kind: "overview" } | { kind: "jobs" } | { kind: "node"; nodeId: string };

type JobResolution = "auto" | "20s" | "2m" | "1h";

const JOB_RESOLUTIONS: { key: JobResolution; label: string }[] = [
  { key: "auto", label: "Auto" },
  { key: "20s", label: "20s" },
  { key: "2m", label: "2m" },
  { key: "1h", label: "1h" },
];
const JOB_CHART_PX_PER_POINT = 1.8;

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
  gpu_indices?: number[];
  pids?: number[];
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

type JobItem = {
  job_key: string;
  user: string;
  node_id: string;
  task_name: string;
  started_at: number;
  last_seen_at: number;
  duration_seconds: number;
  status: string;
  session_count: number;
  gpu_count: number;
  pids: number[];
  gpus: {
    node_id: string;
    gpu_uuid: string;
    gpu_index: number | null;
    gpu_name: string | null;
  }[];
};

type JobPoint = Partial<AnalyticsPoint> & {
  bucket_start: number;
  sampled_at?: number;
  utilization_gpu?: number;
  memory_used_mb?: number;
  power_watts?: number;
  temperature_c?: number;
};

type JobCurveSeries = {
  node_id: string;
  gpu_uuid: string;
  gpu_index: number | null;
  gpu_name: string | null;
  label: string;
  points: JobPoint[];
};

type JobCurve = AnalyticsMeta & {
  source?: "high_res_memory" | "rollup" | "mixed";
  job_key?: string;
  job?: JobItem;
  range_start?: number;
  range_end?: number;
  coverage_start?: number | null;
  coverage_end?: number | null;
  cache_retention_seconds?: number;
  resolution_seconds?: number | null;
  resolution_mode?: string;
  expired?: boolean;
  warnings?: string[];
  series?: JobCurveSeries[];
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
  jobElement: HTMLElement;
  currentRoute: () => Route;
  renderIcons: () => void;
};

const OVERVIEW_RANGES = ["24h", "7d", "30d"];
const NODE_RANGES = ["1h", "24h", "7d", "30d"];
const HEATMAP_HOURS = 12;
const HEATMAP_RANGE = "24h";
const HOUR_SECONDS = 60 * 60;
const NODE_METRICS: { key: NodeMetric; label: string; max?: number }[] = [
  { key: "avg_gpu_utilization", label: "GPU", max: 100 },
  { key: "avg_memory_used_mb", label: "Memory" },
  { key: "avg_power_watts", label: "Power" },
  { key: "avg_temperature_c", label: "Temp", max: 100 },
];
const CHART_COLORS = [
  "--chart-1",
  "--chart-2",
  "--chart-3",
  "--chart-4",
  "--chart-5",
  "--chart-6",
  "--chart-7",
  "--chart-8",
];

export function createAnalyticsController({
  overviewElement,
  nodeElement,
  jobElement,
  currentRoute,
  renderIcons,
}: AnalyticsControllerOptions) {
  let overviewRange = "7d";
  let overviewPayload: OverviewAnalytics | null = null;
  let overviewKey = "";
  let overviewLoading = false;
  let nodeRange = "24h";
  let nodeMetric: NodeMetric = "avg_gpu_utilization";
  const selectedGpuUuids = new Set<string>();
  let nodePayload: NodeAnalytics | null = null;
  let nodeHeatmapPayload: NodeAnalytics | null = null;
  let nodeKey = "";
  let nodeHeatmapKey = "";
  let nodeLoading = false;
  let nodeHeatmapLoading = false;
  let nodeChart: uPlot | null = null;
  let nodeChartResize: ResizeObserver | null = null;
  let jobQuery = "";
  let jobPayload: { enabled: boolean; items?: JobItem[] } | null = null;
  let jobKey = "";
  let jobsLoading = false;
  let selectedJobKey = "";
  let jobCurvePayload: JobCurve | null = null;
  let jobCurveKey = "";
  let jobCurveLoading = false;
  let jobMetric: NodeMetric = "avg_gpu_utilization";
  let jobResolution: JobResolution = "auto";
  const selectedJobGpuUuids = new Set<string>();
  let jobChartExpanded = false;
  let jobChart: uPlot | null = null;
  let jobChartResize: ResizeObserver | null = null;
  let jobModalChart: uPlot | null = null;
  let jobModalChartResize: ResizeObserver | null = null;

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
      updateSelectedGpu(target.dataset.gpuUuid || null);
      renderNode(currentRoute());
      return true;
    }
    if (action === "job-search") {
      submitJobSearch();
      return true;
    }
    if (action === "job-refresh") {
      jobKey = "";
      jobCurveKey = "";
      void fetchJobs();
      if (selectedJobKey) {
        void fetchJobCurve(selectedJobKey);
      }
      return true;
    }
    if (action === "job-select" && target.dataset.jobKey) {
      selectedJobKey = target.dataset.jobKey;
      jobCurvePayload = null;
      jobCurveKey = "";
      selectedJobGpuUuids.clear();
      jobChartExpanded = false;
      renderJobs();
      void fetchJobCurve(selectedJobKey);
      return true;
    }
    if (action === "job-metric" && target.dataset.metric) {
      jobMetric = target.dataset.metric as NodeMetric;
      renderJobs();
      return true;
    }
    if (action === "job-resolution" && target.dataset.resolution) {
      jobResolution = normalizeJobResolution(target.dataset.resolution);
      jobCurvePayload = null;
      jobCurveKey = "";
      jobChartExpanded = false;
      renderJobs();
      if (selectedJobKey) {
        void fetchJobCurve(selectedJobKey);
      }
      return true;
    }
    if (action === "job-gpu") {
      updateSelectedJobGpu(target.dataset.gpuUuid || null);
      renderJobs();
      return true;
    }
    if (action === "job-expand") {
      jobChartExpanded = true;
      renderJobs();
      return true;
    }
    if (action === "job-collapse") {
      jobChartExpanded = false;
      renderJobs();
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

  function handleKeyDown(event: KeyboardEvent) {
    const target = event.target as HTMLElement | null;
    if (event.key === "Enter" && target?.matches("[data-job-query]")) {
      event.preventDefault();
      submitJobSearch();
      return true;
    }
    if (event.key === "Escape" && jobChartExpanded) {
      event.preventDefault();
      jobChartExpanded = false;
      renderJobs();
      return true;
    }
    return false;
  }

  function submitJobSearch() {
    const input = jobElement.querySelector<HTMLInputElement>("[data-job-query]");
    jobQuery = input?.value.trim() || "";
    jobKey = "";
    selectedJobKey = "";
    selectedJobGpuUuids.clear();
    jobChartExpanded = false;
    jobCurvePayload = null;
    jobCurveKey = "";
    renderJobs();
    void fetchJobs();
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
      selectedGpuUuids.clear();
    }
    if (!nodeHeatmapKey.startsWith(`${route.nodeId}:`)) {
      nodeHeatmapPayload = null;
      nodeHeatmapKey = "";
    }
    nodeLoading = true;
    renderNode(route);
    void fetchNodeHeatmap(route);
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

  async function fetchNodeHeatmap(route: Route) {
    if (route.kind !== "node") {
      return;
    }
    const key = `${route.nodeId}:${HEATMAP_RANGE}`;
    if (nodeHeatmapLoading || nodeHeatmapKey === key) {
      return;
    }
    nodeHeatmapLoading = true;
    renderNode(route);
    try {
      const response = await fetch(
        `/api/analytics/node/${encodeURIComponent(route.nodeId)}?range=${encodeURIComponent(HEATMAP_RANGE)}`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        throw new Error(`node heatmap failed: ${response.status}`);
      }
      nodeHeatmapPayload = (await response.json()) as NodeAnalytics;
      nodeHeatmapKey = key;
    } catch {
      nodeHeatmapPayload = { enabled: false };
      nodeHeatmapKey = key;
    } finally {
      nodeHeatmapLoading = false;
      renderNode(route);
      renderIcons();
    }
  }

  async function fetchJobs() {
    const key = jobQuery;
    if (jobsLoading || jobKey === key) {
      return;
    }
    jobsLoading = true;
    renderJobs();
    const params = new URLSearchParams({ limit: "80" });
    if (jobQuery) {
      params.set("q", jobQuery);
    }
    try {
      const response = await fetch(`/api/highres/jobs?${params.toString()}`, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`job search failed: ${response.status}`);
      }
      jobPayload = (await response.json()) as { enabled: boolean; items?: JobItem[] };
      jobKey = key;
      if (!selectedJobKey && jobPayload.items?.length) {
        selectedJobKey = jobPayload.items[0].job_key;
        void fetchJobCurve(selectedJobKey);
      }
    } catch {
      jobPayload = { enabled: false };
      jobKey = key;
    } finally {
      jobsLoading = false;
      renderJobs();
      renderIcons();
    }
  }

  async function fetchJobCurve(key: string) {
    const curveKey = `${key}:${jobResolution}`;
    if (!key || jobCurveLoading || jobCurveKey === curveKey) {
      return;
    }
    jobCurveLoading = true;
    renderJobs();
    try {
      const params = new URLSearchParams({ resolution: jobResolution });
      const response = await fetch(`/api/highres/jobs/${encodeURIComponent(key)}/gpu?${params.toString()}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`job curve failed: ${response.status}`);
      }
      jobCurvePayload = (await response.json()) as JobCurve;
      jobCurveKey = curveKey;
    } catch {
      jobCurvePayload = { enabled: false, series: [] };
      jobCurveKey = curveKey;
    } finally {
      jobCurveLoading = false;
      renderJobs();
      renderIcons();
    }
  }

  function renderOverview() {
    const payload = overviewPayload;
    const disabled = payload && payload.enabled === false;
    overviewElement.innerHTML = `
      <div class="analytics-head">
        <div>
          <span class="section-kicker">History</span>
          <h2>Usage and jobs</h2>
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
    destroyNodeChart();
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
            : nodeBody(payload, nodeHeatmapPayload, nodeHeatmapLoading)
      }
    `;
    if (!disabled && !nodeLoading && payload?.series?.some((item) => item.points.length)) {
      mountNodeChart(payload.series, nodeMetric);
    }
  }

  function renderJobs() {
    destroyJobChart();
    const disabled = jobPayload && jobPayload.enabled === false;
    const jobs = jobPayload?.items || [];
    const selected = jobs.find((item) => item.job_key === selectedJobKey) || jobCurvePayload?.job || null;
    const visibleSeries = selectedJobSeries(jobCurvePayload?.series || []);
    jobElement.innerHTML = `
      <div class="analytics-head">
        <div>
          <span class="section-kicker">Job curves</span>
          <h2>Search workload GPU curves</h2>
          <p>${disabled ? "SQLite history is not enabled" : jobMetaText(jobCurvePayload, jobs.length)}</p>
        </div>
        <div class="analytics-actions job-search-actions">
          <label class="job-search">
            <span>Search</span>
            <input data-job-query type="search" value="${escapeAttr(jobQuery)}" placeholder="user, task, command, pid" />
          </label>
          <div class="job-toolbar-buttons">
            ${metricButtonsForAction(jobMetric, "job-metric")}
            <button class="icon-button" type="button" data-analytics-action="job-search" aria-label="Search jobs" title="Search jobs">
              <i data-lucide="search"></i>
            </button>
            <button class="icon-button" type="button" data-analytics-action="job-refresh" aria-label="Refresh jobs" title="Refresh jobs">
              <i data-lucide="refresh-cw"></i>
            </button>
          </div>
        </div>
      </div>
      <div class="job-feature-note">
        <strong>Supports job search within 7 days.</strong>
        <span>Jobs within 2h and shorter than 1h use high-resolution data; others automatically use compressed rollup precision.</span>
      </div>
      ${
        disabled
          ? disabledAnalytics("Enable DB_PATH to search jobs and render persisted rollup fallback curves.")
          : jobsLoading && !jobPayload
            ? loadingPanel("loading recent jobs")
            : jobsBody(jobs, selected)
      }
      ${jobChartExpanded && visibleSeries.some((item) => item.points.length) ? expandedJobChart(visibleSeries, jobMetric) : ""}
    `;
    if (!disabled && !jobCurveLoading && visibleSeries.some((item) => item.points.length)) {
      mountJobChart(visibleSeries, jobMetric);
    }
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
            <em>GPU hours are weighted by GPU model</em>
          </div>
          ${
            users.length
              ? analyticsTable(
                  ["User", "GPU hours", "Jobs", "Models", "Last seen"],
                  users.slice(0, 8).map((item) => [
                    item.user,
                    fmtNumber(item.weighted_gpu_hours),
                    String(item.job_count),
                    item.top_gpu_models.map((model) => model.name).join(", ") || "n/a",
                    formatTime(item.last_seen_at),
                  ]),
                )
              : emptyInline("no user history in this range")
          }
        </article>
        <article class="analytics-card span-6">
          <div class="card-title">
            <span><i data-lucide="table-2"></i>Job rankings</span>
            <em>Top 8</em>
          </div>
          ${
            jobs.length
              ? analyticsTable(
                  ["Task", "User", "Node", "GPU hours", "Runtime", "Status"],
                  jobs.slice(0, 8).map((item) => [
                    item.task_name,
                    item.user,
                    item.node_id,
                    fmtNumber(item.weighted_gpu_hours),
                    fmtDuration(item.duration_seconds),
                    item.status,
                  ]),
                )
              : emptyInline("no job history in this range")
          }
        </article>
        <div class="analytics-head analytics-inline-head span-12">
          <div>
            <span class="section-kicker">Operations</span>
            <h2>Reservations and off-hour use</h2>
            <p>${metaText(payload)}</p>
          </div>
        </div>
        <article class="analytics-card span-6">
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
        <article class="analytics-card span-6">
          <div class="card-title">
            <span><i data-lucide="moon"></i>Off-hour activity</span>
            <em>Beijing time</em>
          </div>
          ${offHoursCard(offHours)}
        </article>
      </div>
    `;
  }

  function nodeBody(payload: NodeAnalytics | null, heatmapPayload: NodeAnalytics | null, heatmapLoading: boolean) {
    const series = payload?.series || [];
    const heatmap = heatmapPayload?.heatmap || [];
    const hasHeatmap = hourlyHeatmapRows(heatmap, heatmapPayload).some((row) => row.cells.some((cell) => cell.hasData));
    return `
      <div class="analytics-grid">
        <article class="analytics-card span-12">
          <div class="card-title">
            <span><i data-lucide="line-chart"></i>${metricLabel(nodeMetric)} history</span>
            <em><span data-node-selection-summary>${selectionSummary(series)}</span> / ${
              payload?.bucket_seconds ? `${formatBucket(payload.bucket_seconds)} buckets` : "rollup"
            }</em>
          </div>
          ${
            series.some((item) => item.points.length)
              ? lineChart(series, nodeMetric)
            : emptyInline("no rollup points in this range")
          }
        </article>
        <article class="analytics-card span-12 heatmap-card">
          <div class="card-title">
            <span><i data-lucide="activity"></i>GPU Heatmap</span>
            <em>Past ${HEATMAP_HOURS} hours</em>
          </div>
          ${
            heatmapLoading && !heatmapPayload
              ? emptyInline("loading GPU heatmap")
              : hasHeatmap
              ? heatmapChart(heatmap, heatmapPayload)
              : emptyInline("No GPU history for the past 12 hours")
          }
        </article>
      </div>
    `;
  }

  function jobsBody(jobs: JobItem[], selected: JobItem | null) {
    return `
      <div class="analytics-grid">
        <article class="analytics-card span-4 job-list-card">
          <div class="card-title">
            <span><i data-lucide="table-2"></i>Recent jobs</span>
            <em>${jobs.length} shown</em>
          </div>
          ${
            jobs.length
              ? `<div class="job-list">${jobs.map(jobListItem).join("")}</div>`
              : emptyInline("no jobs match the current filter")
          }
        </article>
        <article class="analytics-card span-8">
          <div class="card-title">
            <span><i data-lucide="line-chart"></i>${selected ? escapeHtml(selected.task_name) : "GPU curve"}</span>
            <div class="job-title-controls">
              <em>Resolution</em>
              ${jobResolutionButtons(jobResolution)}
            </div>
          </div>
          ${selected ? jobDetail(selected) : emptyInline("select a job to render its GPU curve")}
          ${
            jobCurveLoading && !jobCurvePayload
              ? emptyInline("loading job curve")
              : jobCurvePayload?.series?.some((item) => item.points.length)
                ? jobLineChart(jobCurvePayload.series, jobMetric)
                : selected
                  ? emptyInline("no curve points are available for this job window")
                  : ""
          }
          ${jobWarnings(jobCurvePayload)}
        </article>
      </div>
    `;
  }

  function jobListItem(item: JobItem) {
    const active = item.job_key === selectedJobKey;
    const gpuLabel = item.gpus.map((gpu) => `GPU${gpu.gpu_index ?? "?"}`).join(", ") || `${item.gpu_count} GPUs`;
    return `
      <button
        class="job-list-item ${active ? "is-active" : ""}"
        type="button"
        data-analytics-action="job-select"
        data-job-key="${escapeAttr(item.job_key)}"
      >
        <strong>${escapeHtml(item.task_name)}</strong>
        <span>${escapeHtml(item.user)} / ${escapeHtml(item.node_id)} / ${escapeHtml(gpuLabel)}</span>
        <small>${fmtDuration(item.duration_seconds)} / ${formatTime(item.last_seen_at)} / ${escapeHtml(item.status)}</small>
      </button>
    `;
  }

  function jobDetail(item: JobItem) {
    const pids = item.pids.length ? item.pids.join(", ") : "n/a";
    return `
      <div class="job-detail-strip">
        <span><b>User</b>${escapeHtml(item.user)}</span>
        <span><b>Node</b>${escapeHtml(item.node_id)}</span>
        <span><b>Runtime</b>${fmtDuration(item.duration_seconds)}</span>
        <span><b>PIDs</b>${escapeHtml(pids)}</span>
      </div>
    `;
  }

  function jobLineChart(series: JobCurveSeries[], metric: NodeMetric) {
    const hasPoints = selectedJobSeries(series).some((item) => item.points.length);
    return `
      <div class="chart-wrap job-chart-wrap">
        <div class="chart-tools">
          <button class="icon-button chart-expand-button" type="button" data-analytics-action="job-expand" aria-label="Expand job curve" title="Expand job curve" ${hasPoints ? "" : "disabled"}>
            <i data-lucide="maximize-2"></i>
          </button>
        </div>
        <div class="chart-plot chart-scroll uplot-theme">
          <div class="line-chart" data-job-chart aria-label="${escapeAttr(metricLabel(metric))} job curve"></div>
        </div>
        ${jobLegend(series)}
      </div>
    `;
  }

  function mountJobChart(series: JobCurveSeries[], metric: NodeMetric) {
    const inline = mountMetricChart({
      target: jobElement.querySelector<HTMLElement>("[data-job-chart]"),
      series,
      metric,
      alignedData: alignedJobChartData,
      valueAt: jobMetricValue,
      labels: (item) => item.label,
      height: chartHeight(),
      minPointWidth: JOB_CHART_PX_PER_POINT,
    });
    if (inline) {
      jobChart = inline.chart;
      jobChartResize = inline.resize;
    }
    const modal = mountMetricChart({
      target: jobElement.querySelector<HTMLElement>("[data-job-chart-modal]"),
      series,
      metric,
      alignedData: alignedJobChartData,
      valueAt: jobMetricValue,
      labels: (item) => item.label,
      height: expandedChartHeight(),
      minPointWidth: 2.2,
    });
    if (modal) {
      jobModalChart = modal.chart;
      jobModalChartResize = modal.resize;
    }
  }

  function destroyJobChart() {
    jobChartResize?.disconnect();
    jobChartResize = null;
    jobChart?.destroy();
    jobChart = null;
    jobModalChartResize?.disconnect();
    jobModalChartResize = null;
    jobModalChart?.destroy();
    jobModalChart = null;
  }

  function jobLegend(series: JobCurveSeries[]) {
    return `
      <div class="chart-legend">
        <button class="${selectedJobGpuUuids.size === 0 ? "is-active" : ""}" type="button" data-analytics-action="job-gpu" data-legend-all="true" aria-pressed="${selectedJobGpuUuids.size === 0 ? "true" : "false"}">
          <b style="background:var(--text)"></b>All
        </button>
        ${series
          .map((item, index) => {
            const selected = isJobGpuSelected(item.gpu_uuid);
            return `
              <button
                class="${selected ? "is-selected" : "is-muted"}"
                type="button"
                data-analytics-action="job-gpu"
                data-gpu-uuid="${escapeAttr(item.gpu_uuid)}"
                aria-pressed="${selected ? "true" : "false"}"
              ><b style="background:var(${CHART_COLORS[index % CHART_COLORS.length]})"></b>${escapeHtml(item.label)}</button>
            `;
          })
          .join("")}
      </div>
    `;
  }

  function expandedJobChart(series: JobCurveSeries[], metric: NodeMetric) {
    return `
      <div class="chart-modal-backdrop" role="presentation">
        <section class="chart-modal" role="dialog" aria-modal="true" aria-label="${escapeAttr(metricLabel(metric))} job curve expanded">
          <div class="chart-modal-head">
            <div>
              <span class="section-kicker">Expanded curve</span>
              <h2>${escapeHtml(metricLabel(metric))} by GPU</h2>
            </div>
            <button class="icon-button" type="button" data-analytics-action="job-collapse" aria-label="Close expanded curve" title="Close expanded curve">
              <i data-lucide="x"></i>
            </button>
          </div>
          <div class="chart-plot chart-scroll uplot-theme">
            <div class="line-chart line-chart-expanded" data-job-chart-modal aria-label="${escapeAttr(metricLabel(metric))} expanded job curve"></div>
          </div>
          ${jobLegend(series)}
        </section>
      </div>
    `;
  }

  function syncSelectedGpu() {
    const series = nodePayload?.series || [];
    if (!selectedGpuUuids.size || !series.length) {
      return;
    }
    const available = new Set(series.map((item) => item.gpu_uuid));
    for (const uuid of Array.from(selectedGpuUuids)) {
      if (!available.has(uuid)) {
        selectedGpuUuids.delete(uuid);
      }
    }
  }

  function updateSelectedGpu(gpuUuid: string | null) {
    if (!gpuUuid) {
      selectedGpuUuids.clear();
      return;
    }
    if (!selectedGpuUuids.size) {
      selectedGpuUuids.add(gpuUuid);
      return;
    }
    if (selectedGpuUuids.has(gpuUuid)) {
      selectedGpuUuids.delete(gpuUuid);
      return;
    }
    selectedGpuUuids.add(gpuUuid);
  }

  function updateSelectedJobGpu(gpuUuid: string | null) {
    if (!gpuUuid) {
      selectedJobGpuUuids.clear();
      return;
    }
    if (!selectedJobGpuUuids.size) {
      selectedJobGpuUuids.add(gpuUuid);
      return;
    }
    if (selectedJobGpuUuids.has(gpuUuid)) {
      selectedJobGpuUuids.delete(gpuUuid);
      return;
    }
    selectedJobGpuUuids.add(gpuUuid);
  }

  function isJobGpuSelected(gpuUuid: string) {
    return selectedJobGpuUuids.size === 0 || selectedJobGpuUuids.has(gpuUuid);
  }

  function selectedJobSeries(series: JobCurveSeries[]) {
    return series.filter((item) => isJobGpuSelected(item.gpu_uuid));
  }

  function metricButtons(selected: NodeMetric) {
    return metricButtonsForAction(selected, "node-metric");
  }

  function lineChart(series: AnalyticsSeries[], metric: NodeMetric) {
    return `
      <div class="chart-wrap">
        <div class="chart-plot uplot-theme">
          <div class="line-chart" data-node-chart aria-label="${escapeAttr(metricLabel(metric))} history"></div>
        </div>
        <div class="chart-legend">
          <button class="${selectedGpuUuids.size === 0 ? "is-active" : ""}" type="button" data-analytics-action="node-gpu" data-legend-all="true" aria-pressed="${selectedGpuUuids.size === 0 ? "true" : "false"}">
            <b style="background:var(--text)"></b>All
          </button>
          ${series
            .map((item, index) => {
              const selected = isGpuSelected(item.gpu_uuid);
              return `
                <button
                  class="${selected ? "is-selected" : "is-muted"}"
                  type="button"
                  data-analytics-action="node-gpu"
                  data-gpu-uuid="${escapeAttr(item.gpu_uuid)}"
                  data-legend-gpu-uuid="${escapeAttr(item.gpu_uuid)}"
                  aria-pressed="${selected ? "true" : "false"}"
                ><b style="background:var(${CHART_COLORS[index % CHART_COLORS.length]})"></b>GPU${item.gpu_index ?? "?"}</button>
              `;
            })
            .join("")}
        </div>
      </div>
    `;
  }

  function mountNodeChart(series: AnalyticsSeries[], metric: NodeMetric) {
    const mounted = mountMetricChart({
      target: nodeElement.querySelector<HTMLElement>("[data-node-chart]"),
      series,
      metric,
      alignedData: alignedChartData,
      valueAt: metricValue,
      labels: (item) => `GPU${item.gpu_index ?? "?"}`,
      show: (item) => isGpuSelected(item.gpu_uuid),
      height: chartHeight(),
    });
    if (mounted) {
      nodeChart = mounted.chart;
      nodeChartResize = mounted.resize;
    }
  }

  function destroyNodeChart() {
    nodeChartResize?.disconnect();
    nodeChartResize = null;
    nodeChart?.destroy();
    nodeChart = null;
  }

  function isGpuSelected(gpuUuid: string) {
    return selectedGpuUuids.size === 0 || selectedGpuUuids.has(gpuUuid);
  }

  function selectionSummary(series: AnalyticsSeries[]) {
    if (!selectedGpuUuids.size) {
      return "all GPUs";
    }
    const selected = series.filter((item) => selectedGpuUuids.has(item.gpu_uuid));
    if (!selected.length) {
      return "all GPUs";
    }
    return selected.map((item) => `GPU${item.gpu_index ?? "?"}`).join(", ");
  }

  return {
    handleClick,
    handleKeyDown,
    fetchOverview,
    fetchNode,
    fetchJobs,
    renderOverview,
    renderNode,
    renderJobs,
  };
}

function metricButtonsForAction(selected: NodeMetric, action: string) {
  return `
    <div class="segmented metric-tabs" role="group">
      ${NODE_METRICS.map(
        (metric) => `
          <button
            class="${metric.key === selected ? "is-active" : ""}"
            type="button"
            data-analytics-action="${action}"
            data-metric="${metric.key}"
            aria-pressed="${metric.key === selected ? "true" : "false"}"
          >${metric.label}</button>
        `,
      ).join("")}
    </div>
  `;
}

function jobResolutionButtons(selected: JobResolution) {
  return `
    <div class="segmented job-resolution-tabs" role="group" aria-label="Job curve resolution">
      ${JOB_RESOLUTIONS.map(
        (item) => `
          <button
            class="${item.key === selected ? "is-active" : ""}"
            type="button"
            data-analytics-action="job-resolution"
            data-resolution="${item.key}"
            aria-pressed="${item.key === selected ? "true" : "false"}"
          >${item.label}</button>
        `,
      ).join("")}
    </div>
  `;
}

function mountMetricChart<T extends { points: P[] }, P>({
  target,
  series,
  metric,
  alignedData,
  valueAt,
  labels,
  show,
  height,
  minPointWidth,
}: {
  target: HTMLElement | null;
  series: T[];
  metric: NodeMetric;
  alignedData: (series: T[], metric: NodeMetric) => { starts: number[]; data: uPlot.AlignedData };
  valueAt: (point: P, metric: NodeMetric) => number;
  labels: (item: T, index: number) => string;
  show?: (item: T) => boolean;
  height: number;
  minPointWidth?: number;
}) {
  if (!target) {
    return null;
  }
  const plotSeries = series.filter((item) => item.points.length);
  const chartData = alignedData(plotSeries, metric);
  if (!chartData.starts.length) {
    return null;
  }
  const metricDef = NODE_METRICS.find((item) => item.key === metric) || NODE_METRICS[0];
  const visibleValues = plotSeries
    .filter((item) => (show ? show(item) : true))
    .flatMap((item) => item.points.map((point) => valueAt(point, metric)));
  const maxValue = metricDef.max || Math.max(1, ...visibleValues) * 1.08;
  const colors = chartColors();
  const resizeTarget = minPointWidth ? target.parentElement || target : target;
  const targetWidth = chartTargetWidth(resizeTarget);
  const width = chartPlotWidth(targetWidth, chartData.starts.length, minPointWidth);
  if (minPointWidth) {
    target.style.width = `${width}px`;
  }
  const css = chartCss();
  const opts: uPlot.Options = {
    width,
    height,
    tzDate: (timestamp) => uPlot.tzDate(new Date(timestamp * 1000), DISPLAY_TIME_ZONE),
    padding: [8, 10, 0, 0],
    scales: {
      x: { time: true },
      y: { range: [0, maxValue] },
    },
    axes: [
      {
        stroke: css.muted,
        grid: { stroke: css.border, width: 1 },
        space: chartAxisSpace(),
        ticks: { stroke: css.border },
        values: (_self, ticks) => sparseAxisLabels(ticks, chartMaxXAxisLabels(width), (value) => formatTime(Number(value))),
      },
      {
        size: chartYAxisSize(metric),
        gap: 8,
        stroke: css.muted,
        grid: { stroke: css.border, width: 1 },
        ticks: { stroke: css.border },
        values: (_self, ticks) => ticks.map((value) => formatMetricTick(Number(value), metric)),
      },
    ],
    cursor: { drag: { x: false, y: false } },
    legend: { show: true },
    series: [
      {},
      ...plotSeries.map((item, index) => ({
        label: labels(item, index),
        show: show ? show(item) : true,
        stroke: colors[index % colors.length],
        width: 2.5,
        points: { show: false },
        spanGaps: false,
        value: (_self: uPlot, value: number | null | undefined) =>
          value === null || value === undefined ? "n/a" : formatMetricTick(Number(value), metric),
      })),
    ],
  };
  const chart = new uPlot(opts, chartData.data, target);
  if (minPointWidth && resizeTarget !== target) {
    enableHorizontalChartDrag(resizeTarget);
  }
  const resize = new ResizeObserver(([entry]) => {
    const nextTargetWidth = Math.max(320, Math.floor(entry.contentRect.width));
    const nextWidth = chartPlotWidth(nextTargetWidth, chartData.starts.length, minPointWidth);
    if (nextWidth !== chart.width) {
      if (minPointWidth) {
        target.style.width = `${nextWidth}px`;
      }
      chart.setSize({ width: nextWidth, height });
    }
  });
  resize.observe(resizeTarget);
  return { chart, resize };
}

function enableHorizontalChartDrag(target: HTMLElement) {
  let dragging = false;
  let startX = 0;
  let startScrollLeft = 0;
  target.addEventListener("pointerdown", (event) => {
    if (target.scrollWidth <= target.clientWidth) {
      return;
    }
    dragging = true;
    startX = event.clientX;
    startScrollLeft = target.scrollLeft;
    target.setPointerCapture(event.pointerId);
  });
  target.addEventListener("pointermove", (event) => {
    if (!dragging) {
      return;
    }
    target.scrollLeft = startScrollLeft - (event.clientX - startX);
    event.preventDefault();
  });
  const stopDragging = (event: PointerEvent) => {
    if (!dragging) {
      return;
    }
    dragging = false;
    if (target.hasPointerCapture(event.pointerId)) {
      target.releasePointerCapture(event.pointerId);
    }
  };
  target.addEventListener("pointerup", stopDragging);
  target.addEventListener("pointercancel", stopDragging);
}

function jobMetaText(payload: JobCurve | null, jobCount: number) {
  if (!payload) {
    return jobCount ? `${jobCount} recent jobs, select one to load curves` : "Recent short jobs use memory cache when covered, otherwise rollup history.";
  }
  const source = payload.source ? sourceLabel(payload.source) : "unknown source";
  const range = payload.range_start && payload.range_end ? `${formatTime(payload.range_start)}-${formatTime(payload.range_end)}` : "selected range";
  const resolution = payload.resolution_seconds ? `${formatBucket(payload.resolution_seconds)} resolution` : "native resolution";
  return `${source} / ${range} / ${resolution}`;
}

function sourceLabel(source: string) {
  if (source === "high_res_memory") {
    return "high-res memory";
  }
  if (source === "mixed") {
    return "mixed coverage";
  }
  return "rollup";
}

function jobWarnings(payload: JobCurve | null) {
  const warnings = payload?.warnings || [];
  if (!warnings.length) {
    return "";
  }
  return `
    <div class="job-warnings">
      ${warnings.map((warning) => `<span><i data-lucide="alert-triangle"></i>${escapeHtml(warning)}</span>`).join("")}
    </div>
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
  const gpuLabel = item.gpu_indices?.length
    ? item.gpu_indices.map((index) => `GPU${index}`).join(", ")
    : `${item.gpu_uuids.length} GPU${item.gpu_uuids.length === 1 ? "" : "s"}`;
  const pidLabel = compactPids(item.pids || []);
  const detailTitle = [
    item.reason,
    item.gpu_uuids.length ? `GPU UUIDs: ${item.gpu_uuids.join(", ")}` : "",
    item.pids?.length ? `PIDs: ${item.pids.join(", ")}` : "",
  ]
    .filter(Boolean)
    .join("\n");
  return `
    <div class="anomaly-card" title="${escapeAttr(detailTitle)}">
      <strong>${escapeHtml(item.task_name)}</strong>
      <span>${escapeHtml(item.user)} / ${escapeHtml(item.node_id)} / ${escapeHtml(gpuLabel)} / ${escapeHtml(pidLabel)}</span>
      <div>
        <b>${fmtNumber(item.gpu_memory_gb)} GiB</b>
        <b>${fmtPct(item.recent_avg_gpu_utilization)} recent GPU</b>
        <b>${fmtDuration(item.idle_tail_seconds)} idle tail</b>
        <b>${fmtDuration(item.duration_seconds)} runtime</b>
        <b>${formatTime(item.last_seen_at)} last seen</b>
      </div>
    </div>
  `;
}

function offHoursCard(item: OffHours | undefined) {
  if (!item) {
    return emptyInline("no off-hour data");
  }
  const topUser = item.top_users?.[0]?.user || "n/a";
  return `
    <p class="offhour-note">${escapeHtml(offHoursInsight(item))}</p>
    <div class="offhour-grid">
      <span><b>${item.night_job_count}</b><small>night jobs</small></span>
      <span><b>${item.weekend_job_count}</b><small>weekend jobs</small></span>
      <span><b>${fmtNumber(item.night_gpu_hours)}</b><small>night GPU h</small></span>
      <span><b>${fmtNumber(item.weekend_gpu_hours)}</b><small>weekend GPU h</small></span>
    </div>
    <div class="top-users">
      <span>Most active <b>${escapeHtml(topUser)}</b></span>
      ${
        (item.top_users || [])
          .slice(0, 4)
          .map((user) => `<span>${escapeHtml(offHourUserLabel(item, user.user))} <b>${user.job_count}</b></span>`)
          .join("") || `<span>no off-hour users</span>`
      }
    </div>
  `;
}

function compactPids(pids: number[]) {
  if (!pids.length) {
    return "pid n/a";
  }
  const sorted = [...pids].sort((a, b) => a - b);
  return sorted.length === 1 ? `pid ${sorted[0]}` : `pid ${sorted[0]} +${sorted.length - 1}`;
}

function offHoursInsight(item: OffHours) {
  const nightBusy = item.night_job_count > 0 || item.night_gpu_hours > 0;
  const weekendBusy = item.weekend_job_count > 0 || item.weekend_gpu_hours > 0;
  if (!nightBusy && !weekendBusy) {
    return "This range stayed quiet outside regular hours.";
  }
  if (!nightBusy && weekendBusy) {
    return "Weekend compute stayed active while nights were quiet.";
  }
  if (nightBusy && !weekendBusy) {
    return "Midnight to 6 a.m. still had training activity.";
  }
  return "Both nights and weekends carried meaningful GPU activity.";
}

function offHourUserLabel(item: OffHours, user: string) {
  const top = item.top_users?.[0]?.user;
  if (user === top && item.weekend_gpu_hours > item.night_gpu_hours) {
    return "Weekend regular";
  }
  if (user === top && item.night_gpu_hours >= item.weekend_gpu_hours) {
    return "Night regular";
  }
  return user;
}

type HourlyHeatCell = {
  start: number;
  end: number;
  avg: number;
  peak: number;
  memoryAvg: number;
  hasData: boolean;
};

type HourlyHeatRow = {
  gpuIndex: number | null;
  cells: HourlyHeatCell[];
};

function heatmapChart(items: AnalyticsHeatmap[], payload: NodeAnalytics | null) {
  const rows = hourlyHeatmapRows(items, payload);
  return `
    <div class="heatmap-scroll">
      <div class="heatmap">
        ${rows.map(heatmapRow).join("")}
        ${heatAxis(rows[0]?.cells.map((cell) => cell.start) || [])}
      </div>
    </div>
    <div class="heat-legend" aria-label="Heatmap utilization legend">
      <b class="heat-legend-ramp"></b>
      <span class="heat-legend-labels">
        <span>idle</span>
        <span>low</span>
        <span>active</span>
        <span>busy</span>
      </span>
    </div>
  `;
}

function hourlyHeatmapRows(items: AnalyticsHeatmap[], payload: NodeAnalytics | null): HourlyHeatRow[] {
  const end = Math.ceil((payload?.range_end || payload?.generated_at || Date.now() / 1000) / HOUR_SECONDS) * HOUR_SECONDS;
  const starts = Array.from({ length: HEATMAP_HOURS }, (_item, index) => end - (HEATMAP_HOURS - index) * HOUR_SECONDS);
  return items.map((item) => ({
    gpuIndex: item.gpu_index,
    cells: starts.map((start) => hourlyHeatCell(item, start)),
  }));
}

function hourlyHeatCell(item: AnalyticsHeatmap, start: number): HourlyHeatCell {
  let samples = 0;
  let gpuWeighted = 0;
  let memoryWeighted = 0;
  let peak = 0;
  for (const bucket of item.buckets) {
    if (bucket.bucket_start < start || bucket.bucket_start >= start + HOUR_SECONDS) {
      continue;
    }
    const weight = Math.max(1, bucket.sample_count || 0);
    samples += weight;
    gpuWeighted += (bucket.avg_gpu_utilization || 0) * weight;
    memoryWeighted += (bucket.avg_memory_used_mb || 0) * weight;
    peak = Math.max(peak, bucket.max_gpu_utilization || 0);
  }
  return {
    start,
    end: start + HOUR_SECONDS,
    avg: samples ? gpuWeighted / samples : 0,
    peak,
    memoryAvg: samples ? memoryWeighted / samples : 0,
    hasData: samples > 0,
  };
}

function heatmapRow(row: HourlyHeatRow) {
  return `
    <div class="heat-row-label">GPU${row.gpuIndex ?? "?"}</div>
    <div class="heat-row">
      ${row.cells.map((cell) => heatmapCell(row, cell)).join("")}
    </div>
  `;
}

function heatmapCell(row: HourlyHeatRow, cell: HourlyHeatCell) {
  const gpuLabel = `GPU${row.gpuIndex ?? "?"}`;
  const tooltip = cell.hasData
    ? [
        gpuLabel,
        `${heatFullTime(cell.start)}-${heatClock(cell.end)}`,
        `GPU avg ${fmtPct(cell.avg)} · peak ${fmtPct(cell.peak)}`,
        `Mem avg ${fmtGiB(cell.memoryAvg)}`,
      ].join("\n")
    : [gpuLabel, `${heatFullTime(cell.start)}-${heatClock(cell.end)}`, "No data"].join("\n");
  return `
    <span
      class="heat-cell ${cell.hasData ? "" : "is-missing"}"
      tabindex="0"
      role="img"
      aria-label="${escapeMultilineAttr(tooltip)}"
      data-tooltip="${escapeMultilineAttr(tooltip)}"
      style="${cell.hasData ? `background:${heatColor(cell.avg)}` : ""}"
    ></span>
  `;
}

function metricValue(point: AnalyticsPoint, metric: NodeMetric) {
  return Number(point[metric] || 0);
}

function metricLabel(metric: NodeMetric) {
  return NODE_METRICS.find((item) => item.key === metric)?.label || "GPU";
}

function alignedChartData(series: AnalyticsSeries[], metric: NodeMetric) {
  const starts = Array.from(
    new Set(series.flatMap((item) => item.points.map((point) => point.bucket_start))),
  ).sort((a, b) => a - b);
  const data: uPlot.AlignedData = [
    starts,
    ...series.map((item) => {
      const values = new Map(item.points.map((point) => [point.bucket_start, metricValue(point, metric)]));
      return starts.map((start) => values.get(start) ?? null);
    }),
  ];
  return { starts, data };
}

function alignedJobChartData(series: JobCurveSeries[], metric: NodeMetric) {
  const starts = Array.from(
    new Set(series.flatMap((item) => item.points.map((point) => point.bucket_start))),
  ).sort((a, b) => a - b);
  const data: uPlot.AlignedData = [
    starts,
    ...series.map((item) => {
      const values = new Map(item.points.map((point) => [point.bucket_start, jobMetricValue(point, metric)]));
      return starts.map((start) => values.get(start) ?? null);
    }),
  ];
  return { starts, data };
}

function jobMetricValue(point: JobPoint, metric: NodeMetric) {
  if (metric === "avg_gpu_utilization") {
    return Number(point.avg_gpu_utilization ?? point.utilization_gpu ?? 0);
  }
  if (metric === "avg_memory_used_mb") {
    return Number(point.avg_memory_used_mb ?? point.memory_used_mb ?? 0);
  }
  if (metric === "avg_power_watts") {
    return Number(point.avg_power_watts ?? point.power_watts ?? 0);
  }
  return Number(point.avg_temperature_c ?? point.temperature_c ?? 0);
}

function chartCss() {
  const styles = getComputedStyle(document.documentElement);
  return {
    border: styles.getPropertyValue("--chart-grid").trim() || styles.getPropertyValue("--border").trim(),
    muted: styles.getPropertyValue("--chart-axis").trim() || styles.getPropertyValue("--muted").trim(),
  };
}

function chartColors() {
  const styles = getComputedStyle(document.documentElement);
  return CHART_COLORS.map((token) => styles.getPropertyValue(token).trim()).filter(Boolean);
}

function chartHeight() {
  return window.matchMedia("(max-width: 760px)").matches ? 240 : 320;
}

function expandedChartHeight() {
  return window.matchMedia("(max-width: 760px)").matches ? Math.floor(window.innerHeight * 0.58) : Math.min(560, Math.floor(window.innerHeight * 0.58));
}

function normalizeJobResolution(value: string): JobResolution {
  return JOB_RESOLUTIONS.some((item) => item.key === value) ? (value as JobResolution) : "auto";
}

function chartTargetWidth(target: HTMLElement) {
  const styles = getComputedStyle(target);
  const padding = Number.parseFloat(styles.paddingLeft) + Number.parseFloat(styles.paddingRight);
  const measuredWidth = target.clientWidth || target.parentElement?.clientWidth || 760;
  return Math.max(320, Math.floor(measuredWidth - padding));
}

function chartPlotWidth(targetWidth: number, pointCount: number, minPointWidth?: number) {
  if (!minPointWidth || pointCount <= 1) {
    return targetWidth;
  }
  return Math.max(targetWidth, Math.ceil(pointCount * minPointWidth));
}

function chartAxisSpace() {
  return window.matchMedia("(max-width: 760px)").matches ? 108 : 92;
}

function chartMaxXAxisLabels(width: number) {
  return Math.max(2, Math.floor(width / chartAxisSpace()));
}

function chartYAxisSize(metric: NodeMetric) {
  if (metric === "avg_memory_used_mb") {
    return 82;
  }
  if (metric === "avg_power_watts") {
    return 66;
  }
  return 58;
}

function sparseAxisLabels<T>(ticks: T[], maxLabels: number, format: (value: T) => string) {
  const step = Math.max(1, Math.ceil(ticks.length / maxLabels));
  return ticks.map((value, index) => (index === 0 || index === ticks.length - 1 || index % step === 0 ? format(value) : ""));
}

function formatMetricTick(value: number, metric: NodeMetric) {
  if (metric === "avg_memory_used_mb") {
    return fmtGiB(value);
  }
  if (metric === "avg_gpu_utilization") {
    return fmtPct(value);
  }
  if (metric === "avg_power_watts") {
    return `${value.toFixed(0)} W`;
  }
  return `${value.toFixed(0)}°C`;
}

function heatAxis(starts: number[]) {
  if (!starts.length) {
    return "";
  }
  return `
    <div class="heat-axis-spacer"></div>
    <div class="heat-axis">
      ${starts.map((start) => `<span>${escapeHtml(heatHour(start))}</span>`).join("")}
    </div>
  `;
}

function heatHour(epochSeconds: number) {
  return heatParts(epochSeconds).hour;
}

function heatColor(value: number) {
  if (value < 5) return "var(--surface-sunken)";
  if (value < 35) return interpolateColor("#dcefe2", "#8bd8ad", (value - 5) / 30);
  if (value < 70) return interpolateColor("#8bd8ad", "#3ebc8c", (value - 35) / 35);
  if (value < 90) return interpolateColor("#3ebc8c", "#168ca0", (value - 70) / 20);
  return interpolateColor("#168ca0", "#0a5365", (value - 90) / 10);
}

function heatFullTime(epochSeconds: number) {
  const parts = heatParts(epochSeconds);
  return `${parts.month}/${parts.day} ${parts.hour}:${parts.minute}`;
}

function heatClock(epochSeconds: number) {
  const parts = heatParts(epochSeconds);
  return `${parts.hour}:${parts.minute}`;
}

function heatParts(epochSeconds: number) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: DISPLAY_TIME_ZONE,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(epochSeconds * 1000));
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value || "00";
  return {
    month: value("month"),
    day: value("day"),
    hour: value("hour"),
    minute: value("minute"),
  };
}

function escapeMultilineAttr(value: string) {
  return escapeHtml(value).replace(/\n/g, "&#10;");
}

function interpolateColor(from: string, to: string, amount: number) {
  const start = parseHexColor(from);
  const end = parseHexColor(to);
  const t = Math.max(0, Math.min(1, amount));
  return `rgb(${Math.round(start[0] + (end[0] - start[0]) * t)}, ${Math.round(
    start[1] + (end[1] - start[1]) * t,
  )}, ${Math.round(start[2] + (end[2] - start[2]) * t)})`;
}

function parseHexColor(value: string): [number, number, number] {
  return [
    Number.parseInt(value.slice(1, 3), 16),
    Number.parseInt(value.slice(3, 5), 16),
    Number.parseInt(value.slice(5, 7), 16),
  ];
}

function metaText(payload: AnalyticsMeta | null) {
  if (!payload?.generated_at) {
    return "waiting for SQLite rollups";
  }
  const range =
    payload.range_start && payload.range_end
      ? `${formatTime(payload.range_start)} - ${formatTime(payload.range_end)}`
      : "selected range";
  return `${range} / generated ${formatTime(payload.generated_at)} / ${payload.timezone || DISPLAY_TIME_ZONE}`;
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
