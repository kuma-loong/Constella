import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { fmtPct } from "./format";
import type { ClusterSnapshot, NodeSnapshot } from "./types";

const PROFILE = "nvidia.gpm.v1";
const COLORS = [
  "--chart-1",
  "--chart-2",
  "--chart-3",
  "--chart-4",
  "--chart-5",
  "--chart-6",
  "--chart-7",
  "--chart-8",
];
const COLLAPSE_KEY = "constella.performance.collapsed";
const LAYOUT_KEY = "constella.performance.layout";

type MetricDefinition = { id: string; label: string; description: string };
type MetricGroup = { id: string; label: string; metrics: MetricDefinition[] };

const GROUPS: MetricGroup[] = [
  {
    id: "compute",
    label: "Compute",
    metrics: [
      { id: "nvidia.gpm.sm_active", label: "SM Active", description: "Busy SM share" },
      { id: "nvidia.gpm.sm_occupancy", label: "SM Occupancy", description: "Active warps vs theoretical maximum" },
      { id: "nvidia.gpm.tensor_active", label: "Tensor Active", description: "All Tensor Core instruction families" },
    ],
  },
  {
    id: "memory",
    label: "Memory",
    metrics: [
      { id: "nvidia.gpm.dram_bw_active", label: "DRAM Bandwidth", description: "Used bandwidth vs theoretical maximum" },
    ],
  },
  {
    id: "pipelines",
    label: "Non-Tensor Pipelines",
    metrics: [
      { id: "nvidia.gpm.fp16_non_tensor_active", label: "FP16", description: "Non-Tensor FP16 activity" },
      { id: "nvidia.gpm.fp32_non_tensor_active", label: "FP32", description: "Non-Tensor FP32 activity" },
      { id: "nvidia.gpm.fp64_non_tensor_active", label: "FP64", description: "Non-Tensor FP64 activity" },
    ],
  },
];
type MetricSummary = {
  avg: number | null;
  min: number | null;
  max: number | null;
  p95: number | null;
  sample_count: number;
  expected_count: number;
  coverage: number;
};

type MetricPayload = {
  points: [number, number | null][];
  summary: MetricSummary;
};

type PerformanceSeries = {
  node_id: string;
  gpu_uuid: string;
  gpu_index: number;
  name: string;
  status: string;
  metrics: Record<string, MetricPayload>;
};

type PerformancePayload = {
  enabled: boolean;
  profile: string;
  since: number;
  until: number;
  metrics: string[];
  series: PerformanceSeries[];
};

type Selection = { from: number; to: number };

export function PerformancePage({ snapshot, visible }: { snapshot: ClusterSnapshot | null; visible: boolean }) {
  const initial = useMemo(() => new URLSearchParams(window.location.search), []);
  const [nodeId, setNodeId] = useState(initial.get("node") || "");
  const [gpuUuids, setGpuUuids] = useState<Set<string>>(() => {
    const gpu = initial.get("gpu");
    return new Set(gpu ? [gpu] : []);
  });
  const [rangeSeconds, setRangeSeconds] = useState(15 * 60);
  const [live, setLive] = useState(true);
  const [layout, setLayout] = useState<1 | 2>(() => window.localStorage.getItem(LAYOUT_KEY) === "1" ? 1 : 2);
  const [collapsed, setCollapsed] = useState<Set<string>>(readCollapsed);
  const [payload, setPayload] = useState<PerformancePayload | null>(null);
  const [selectionPayload, setSelectionPayload] = useState<PerformancePayload | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef(0);

  const nodes = snapshot?.nodes || [];
  const selectedNode = nodes.find((node) => node.node_id === nodeId) || null;
  const selectedNodeId = selectedNode?.node_id || "";
  const selectedNodeCapable = Boolean(selectedNode?.performance_profiles?.includes(PROFILE));
  const requestedMetrics = useMemo(
    () => GROUPS.flatMap((group) =>
      collapsed.has(`group:${group.id}`)
        ? []
        : group.metrics.filter((metric) => !collapsed.has(`chart:${metric.id}`)).map((metric) => metric.id),
    ),
    [collapsed],
  );
  const metricQuery = requestedMetrics.join(",");
  const maxPoints = Math.max(
    250,
    Math.min(1500, Math.floor(24000 / Math.max(1, gpuUuids.size * requestedMetrics.length))),
  );
  const liveRefreshMs = rangeSeconds <= 15 * 60 ? 2000 : rangeSeconds <= 60 * 60 ? 5000 : 10000;

  useEffect(() => {
    if (!nodes.length || nodes.some((node) => node.node_id === nodeId)) {
      return;
    }
    const capable = nodes.find((node) => node.performance_profiles?.includes(PROFILE));
    setNodeId((capable || nodes[0]).node_id);
  }, [nodeId, nodes]);

  useEffect(() => {
    if (!selectedNode) {
      return;
    }
    const available = new Set(selectedNode.gpus.map((gpu) => gpu.uuid));
    setGpuUuids((previous) => {
      const kept = new Set(Array.from(previous).filter((uuid) => available.has(uuid)));
      if (kept.size) {
        return kept;
      }
      const saved = readGpuSelection(selectedNode.node_id).filter((uuid) => available.has(uuid));
      return new Set(saved.length ? saved : selectedNode.gpus.map((gpu) => gpu.uuid));
    });
    setSelection(null);
    setSelectionPayload(null);
  }, [selectedNode?.node_id]);

  useEffect(() => {
    if (!selectedNode) {
      return;
    }
    window.localStorage.setItem(
      `constella.performance.gpus.${selectedNode.node_id}`,
      JSON.stringify(Array.from(gpuUuids)),
    );
    const params = new URLSearchParams();
    params.set("node", selectedNode.node_id);
    if (gpuUuids.size === 1) {
      params.set("gpu", Array.from(gpuUuids)[0]);
    }
    window.history.replaceState(null, "", `/performance?${params.toString()}`);
  }, [gpuUuids, selectedNode?.node_id]);

  const fetchPerformance = useCallback(async (signal?: AbortSignal) => {
    if (!visible || !selectedNodeId || !selectedNodeCapable || !gpuUuids.size || !metricQuery) {
      setPayload(null);
      return;
    }
    const request = ++requestRef.current;
    const until = Date.now() / 1000;
    const params = new URLSearchParams({
      node_id: selectedNodeId,
      gpu_uuid: Array.from(gpuUuids).join(","),
      metrics: metricQuery,
      since: String(until - rangeSeconds),
      until: String(until),
      max_points: String(maxPoints),
    });
    setLoading(true);
    try {
      const response = await fetch(`/api/highres/performance?${params.toString()}`, {
        cache: "no-store",
        signal,
      });
      if (!response.ok) {
        throw new Error(`performance request failed: ${response.status}`);
      }
      if (request === requestRef.current) {
        setPayload((await response.json()) as PerformancePayload);
        setError(null);
      }
    } catch (requestError) {
      if ((requestError as Error).name !== "AbortError") {
        if (request === requestRef.current) {
          setError((requestError as Error).message);
        }
      }
    } finally {
      if (request === requestRef.current) {
        setLoading(false);
      }
    }
  }, [gpuUuids, maxPoints, metricQuery, rangeSeconds, selectedNodeCapable, selectedNodeId, visible]);

  useEffect(() => {
    const controller = new AbortController();
    void fetchPerformance(controller.signal);
    const timer = live && visible
      ? window.setInterval(() => void fetchPerformance(controller.signal), liveRefreshMs)
      : 0;
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [fetchPerformance, live, liveRefreshMs, visible]);

  useEffect(() => {
    if (!selection || !selectedNode || !gpuUuids.size || !metricQuery) {
      setSelectionPayload(null);
      return;
    }
    const controller = new AbortController();
    const params = new URLSearchParams({
      node_id: selectedNode.node_id,
      gpu_uuid: Array.from(gpuUuids).join(","),
      metrics: metricQuery,
      since: String(selection.from),
      until: String(selection.to),
      summary_only: "true",
    });
    void fetch(`/api/highres/performance?${params.toString()}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then((response) => response.json() as Promise<PerformancePayload>)
      .then(setSelectionPayload)
      .catch(() => undefined);
    return () => controller.abort();
  }, [gpuUuids, metricQuery, selectedNode?.node_id, selection]);

  const selectRange = useCallback((next: Selection) => {
    setSelection(next);
    setLive(false);
  }, []);

  function toggleCollapsed(id: string) {
    setCollapsed((previous) => {
      const next = new Set(previous);
      next.has(id) ? next.delete(id) : next.add(id);
      window.localStorage.setItem(COLLAPSE_KEY, JSON.stringify(Array.from(next)));
      return next;
    });
  }

  function setAllCollapsed(value: boolean) {
    const next = new Set<string>();
    if (value) {
      for (const group of GROUPS) {
        next.add(`group:${group.id}`);
      }
    }
    setCollapsed(next);
    window.localStorage.setItem(COLLAPSE_KEY, JSON.stringify(Array.from(next)));
  }

  function resumeLive() {
    setSelection(null);
    setSelectionPayload(null);
    setLive(true);
    void fetchPerformance();
  }

  return (
    <section class="performance-page" hidden={!visible}>
      <div class="performance-head">
        <div>
          <span class="section-kicker">High-resolution hardware telemetry</span>
          <h2>Accelerator performance</h2>
          <p>Compare synchronized compute, memory and non-Tensor pipelines. Drag any chart to analyze one interval.</p>
        </div>
        <div class="performance-actions">
          <button type="button" class="section-toggle" onClick={() => setAllCollapsed(false)}>Expand all</button>
          <button type="button" class="section-toggle" onClick={() => setAllCollapsed(true)}>Collapse all</button>
        </div>
      </div>

      <div class="performance-toolbar">
        <label>
          <span>Node</span>
          <select value={nodeId} onChange={(event) => setNodeId(event.currentTarget.value)}>
            {nodes.map((node) => (
              <option key={node.node_id} value={node.node_id}>
                {node.node_id} · {profileLabel(node)}
              </option>
            ))}
          </select>
        </label>
        <div class="gpu-switcher" aria-label="GPU selection">
          <span>GPU</span>
          <div>
            {(selectedNode?.gpus || []).map((gpu) => (
              <button
                key={gpu.uuid}
                type="button"
                class={gpuUuids.has(gpu.uuid) ? "is-active" : ""}
                aria-pressed={gpuUuids.has(gpu.uuid)}
                onClick={() => setGpuUuids(toggleValue(gpuUuids, gpu.uuid))}
              >
                GPU{gpu.index}
              </button>
            ))}
            <button
              type="button"
              class={Boolean(selectedNode?.gpus.length) && gpuUuids.size === selectedNode?.gpus.length ? "is-active" : ""}
              onClick={() => setGpuUuids(new Set(selectedNode?.gpus.map((gpu) => gpu.uuid) || []))}
            >All</button>
            <button type="button" onClick={() => setGpuUuids(new Set())}>Clear</button>
          </div>
        </div>
        <div class="performance-ranges" role="group" aria-label="Time range">
          {[5, 15, 60, 120].map((minutes) => (
            <button
              key={minutes}
              type="button"
              class={rangeSeconds === minutes * 60 ? "is-active" : ""}
              onClick={() => setRangeSeconds(minutes * 60)}
            >
              {minutes < 60 ? `${minutes}m` : `${minutes / 60}h`}
            </button>
          ))}
        </div>
        <button type="button" class={`live-control ${live ? "is-active" : ""}`} onClick={resumeLive}>
          {live ? "Live" : "Resume live"}
        </button>
        <button
          type="button"
          class="layout-control"
          onClick={() => {
            const next = layout === 2 ? 1 : 2;
            setLayout(next);
            window.localStorage.setItem(LAYOUT_KEY, String(next));
          }}
        >
          {layout} column{layout === 1 ? "" : "s"}
        </button>
      </div>

      {selection ? (
        <div class="selection-strip">
          <span>Selected interval</span>
          <strong>{formatClock(selection.from)} to {formatClock(selection.to)}</strong>
          <button type="button" onClick={() => { setSelection(null); setSelectionPayload(null); }}>Clear</button>
        </div>
      ) : null}

      {!selectedNode ? (
        <div class="empty-panel">Waiting for cluster nodes</div>
      ) : !selectedNode.performance_profiles?.includes(PROFILE) ? (
        <div class="empty-panel">Detailed performance is not available for this hardware profile. Base monitoring remains active.</div>
      ) : !gpuUuids.size ? (
        <div class="empty-panel">Select at least one GPU.</div>
      ) : error ? (
        <div class="empty-panel">{error}</div>
      ) : payload?.enabled === false ? (
        <div class="empty-panel">High-resolution performance caching is disabled for this service.</div>
      ) : (
        <div class={`performance-groups layout-${layout} ${loading && !payload ? "is-loading" : ""}`}>
          {GROUPS.map((group) => {
            const groupId = `group:${group.id}`;
            const groupCollapsed = collapsed.has(groupId);
            return (
              <section key={group.id} class={`performance-group group-${group.id} ${groupCollapsed ? "is-collapsed" : ""}`}>
                <button type="button" class="performance-group-head" onClick={() => toggleCollapsed(groupId)}>
                  <span>{group.label}</span>
                  <small>{groupCollapsed ? "Expand" : `${group.metrics.length} metric${group.metrics.length === 1 ? "" : "s"} · Collapse`}</small>
                </button>
                {!groupCollapsed ? (
                  <div class="performance-chart-grid">
                    {group.metrics.map((metric) => {
                      const chartId = `chart:${metric.id}`;
                      return (
                        <PerformanceChart
                          key={metric.id}
                          definition={metric}
                          series={payload?.series || []}
                          statsSeries={selectionPayload?.series || payload?.series || []}
                          collapsed={collapsed.has(chartId)}
                          selection={selection}
                          onSelection={selectRange}
                          onToggle={() => toggleCollapsed(chartId)}
                        />
                      );
                    })}
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      )}
    </section>
  );
}

function PerformanceChart({
  definition,
  series,
  statsSeries,
  collapsed,
  selection,
  onSelection,
  onToggle,
}: {
  definition: MetricDefinition;
  series: PerformanceSeries[];
  statsSeries: PerformanceSeries[];
  collapsed: boolean;
  selection: Selection | null;
  onSelection: (selection: Selection) => void;
  onToggle: () => void;
}) {
  const targetRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<uPlot | null>(null);
  const resizeRef = useRef<ResizeObserver | null>(null);
  const aligned = useMemo(() => alignSeries(series, definition.id), [definition.id, series]);

  useEffect(() => {
    if (collapsed || !targetRef.current || aligned.timestamps.length === 0) {
      return;
    }
    const target = targetRef.current;
    const css = getComputedStyle(document.documentElement);
    const chart = new uPlot(
      {
        width: Math.max(320, target.clientWidth),
        height: 238,
        padding: [8, 8, 0, 0],
        scales: { x: { time: true }, y: { range: [0, 100] } },
        axes: [
          { stroke: css.getPropertyValue("--chart-axis").trim(), grid: { stroke: css.getPropertyValue("--chart-grid").trim() } },
          { stroke: css.getPropertyValue("--chart-axis").trim(), grid: { stroke: css.getPropertyValue("--chart-grid").trim() }, values: (_plot, values) => values.map((value) => `${value}%`) },
        ],
        cursor: {
          drag: { x: true, y: false },
          sync: { key: "constella-performance" },
        },
        hooks: {
          setSelect: [
            (plot) => {
              if (plot.select.width < 4) {
                return;
              }
              const from = plot.posToVal(plot.select.left, "x");
              const to = plot.posToVal(plot.select.left + plot.select.width, "x");
              onSelection({ from: Math.min(from, to), to: Math.max(from, to) });
            },
          ],
        },
        legend: { show: false },
        series: [
          {},
          ...series.map((item, index) => ({
            label: `GPU${item.gpu_index}`,
            stroke: css.getPropertyValue(COLORS[index % COLORS.length]).trim(),
            width: 2,
            spanGaps: false,
            points: { show: false },
            value: (_plot: uPlot, value: number | null | undefined) => value == null ? "n/a" : fmtPct(value),
          })),
        ],
      },
      aligned.data,
      target,
    );
    chartRef.current = chart;
    const resize = new ResizeObserver(([entry]) => {
      chart.setSize({ width: Math.max(320, Math.floor(entry.contentRect.width)), height: 238 });
    });
    resize.observe(target);
    resizeRef.current = resize;
    return () => {
      resize.disconnect();
      chart.destroy();
      chartRef.current = null;
    };
  }, [collapsed, definition.id, series.map((item) => item.gpu_uuid).join(",")]);

  useEffect(() => {
    chartRef.current?.setData(aligned.data);
  }, [aligned]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) {
      return;
    }
    if (!selection) {
      chart.setSelect({ left: 0, top: 0, width: 0, height: chart.bbox.height }, false);
      return;
    }
    const left = chart.valToPos(selection.from, "x");
    const right = chart.valToPos(selection.to, "x");
    chart.setSelect({ left, top: 0, width: Math.max(0, right - left), height: chart.bbox.height }, false);
  }, [selection]);

  return (
    <article class={`performance-chart ${collapsed ? "is-collapsed" : ""}`}>
      <button type="button" class="performance-chart-head" onClick={onToggle}>
        <span><strong>{definition.label}</strong><small>{definition.description}</small></span>
        <em>{collapsed ? "Expand" : "Collapse"}</em>
      </button>
      {!collapsed ? (
        <>
          {aligned.timestamps.length ? <div ref={targetRef} class="performance-plot uplot-theme" /> : <div class="performance-no-data">No valid samples in this range</div>}
          <div class="performance-summary">
            {statsSeries.map((item, index) => {
              const summary = item.metrics[definition.id]?.summary;
              return (
                <span key={item.gpu_uuid}>
                  <b style={{ background: `var(${COLORS[index % COLORS.length]})` }} />
                  <strong>GPU{item.gpu_index}</strong>
                  <small>Avg {formatStat(summary?.avg)} · Peak {formatStat(summary?.max)} · P95 {formatStat(summary?.p95)} · Coverage {summary?.coverage ?? 0}%</small>
                </span>
              );
            })}
          </div>
        </>
      ) : null}
    </article>
  );
}

function alignSeries(series: PerformanceSeries[], metric: string) {
  const timestamps = Array.from(
    new Set(series.flatMap((item) => (item.metrics[metric]?.points || []).map((point) => point[0]))),
  ).sort((a, b) => a - b);
  const data: uPlot.AlignedData = [
    timestamps,
    ...series.map((item) => {
      const values = new Map(item.metrics[metric]?.points || []);
      return timestamps.map((timestamp) => values.get(timestamp) ?? null);
    }),
  ];
  return { timestamps, data };
}

function toggleValue(values: Set<string>, value: string) {
  const next = new Set(values);
  next.has(value) ? next.delete(value) : next.add(value);
  return next;
}

function readCollapsed() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(COLLAPSE_KEY) || "[]");
    return new Set<string>(Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : []);
  } catch {
    return new Set<string>();
  }
}

function readGpuSelection(nodeId: string): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(`constella.performance.gpus.${nodeId}`) || "[]");
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function formatClock(timestamp: number) {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(timestamp * 1000);
}

function formatStat(value: number | null | undefined) {
  return value == null ? "n/a" : fmtPct(value);
}

function profileLabel(node: NodeSnapshot) {
  const profiles = node.performance_profiles || [];
  if (profiles.includes(PROFILE)) {
    return "NVIDIA GPM";
  }
  return profiles.length ? `unsupported: ${profiles.join(", ")}` : "base telemetry";
}
