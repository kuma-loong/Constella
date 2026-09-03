import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { fmtMiBPerSecond, fmtPct } from "./format";
import { GuideDrawer } from "./guides/GuideDrawer";
import type { GuideLocale } from "./guides/types";
import {
  PERFORMANCE_GROUPS,
  PERFORMANCE_METRIC_IDS,
  DEFAULT_PERFORMANCE_METRIC_IDS,
  type MetricDefinition,
  type PerformanceMetricId,
} from "./performance-metrics";
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
const GUIDE_LOCALE_KEY = "constella.guide.locale";
const CHART_HEIGHT = 300;
const POINTS_PER_SERIES = 1000;
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
  const [collapsed, setCollapsed] = useState<Set<string>>(readCollapsed);
  const [payload, setPayload] = useState<PerformancePayload | null>(null);
  const [selectionPayload, setSelectionPayload] = useState<PerformancePayload | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [guideOpen, setGuideOpen] = useState(initial.get("guide") === "performance");
  const [guideMetricId, setGuideMetricId] = useState<PerformanceMetricId | null>(() => parseMetricId(initial.get("metric")));
  const [guideLocale, setGuideLocale] = useState<GuideLocale>(() => readGuideLocale(initial));
  const requestRef = useRef(0);
  const guideTriggerRef = useRef<HTMLElement | null>(null);

  const nodes = snapshot?.nodes || [];
  const selectedNode = nodes.find((node) => node.node_id === nodeId) || null;
  const selectedNodeId = selectedNode?.node_id || "";
  const selectedNodeCapable = Boolean(selectedNode?.performance_profiles?.includes(PROFILE));
  const visibleGroups = useMemo(() => {
    const discovered = new Set<string>();
    for (const gpu of selectedNode?.gpus || []) {
      if (gpuUuids.size && !gpuUuids.has(gpu.uuid)) {
        continue;
      }
      for (const metric of gpu.performance?.supported_metrics || Object.keys(gpu.performance?.metrics || {})) {
        discovered.add(metric);
      }
    }
    const supported = discovered.size ? discovered : new Set<string>(DEFAULT_PERFORMANCE_METRIC_IDS);
    return PERFORMANCE_GROUPS
      .map((group) => ({ ...group, metrics: group.metrics.filter((metric) => supported.has(metric.id)) }))
      .filter((group) => group.metrics.length);
  }, [gpuUuids, selectedNode]);
  const requestedMetrics = useMemo(
    () => visibleGroups.flatMap((group) =>
      collapsed.has(`group:${group.id}`)
        ? []
        : group.metrics.filter((metric) => !collapsed.has(`chart:${metric.id}`)).map((metric) => metric.id),
    ),
    [collapsed, visibleGroups],
  );
  const metricQuery = requestedMetrics.join(",");
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
    if (!visible || !selectedNode) {
      return;
    }
    window.localStorage.setItem(
      `constella.performance.gpus.${selectedNode.node_id}`,
      JSON.stringify(Array.from(gpuUuids)),
    );
    const params = new URLSearchParams(window.location.search);
    params.set("node", selectedNode.node_id);
    if (gpuUuids.size === 1) {
      params.set("gpu", Array.from(gpuUuids)[0]);
    } else {
      params.delete("gpu");
    }
    window.history.replaceState(window.history.state, "", `/performance?${params.toString()}`);
  }, [visible, gpuUuids, selectedNode?.node_id]);

  useEffect(() => {
    const syncFromLocation = () => {
      const params = new URLSearchParams(window.location.search);
      const nextOpen = params.get("guide") === "performance";
      setGuideOpen(nextOpen);
      setGuideMetricId(nextOpen ? parseMetricId(params.get("metric")) : null);
      const nextLocale = parseGuideLocale(params.get("lang"));
      if (nextLocale) {
        setGuideLocale(nextLocale);
        window.localStorage.setItem(GUIDE_LOCALE_KEY, nextLocale);
      }
    };
    window.addEventListener("popstate", syncFromLocation);
    return () => window.removeEventListener("popstate", syncFromLocation);
  }, []);

  useEffect(() => {
    if (!guideOpen || !visible) {
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLButtonElement>("[data-guide-close]")?.focus();
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeGuide();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const drawer = document.querySelector<HTMLElement>(".guide-drawer");
      const focusable = drawer
        ? Array.from(drawer.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'))
        : [];
      if (!focusable.length) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [guideOpen, visible]);

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
      max_points: String(POINTS_PER_SERIES),
    });
    setLoading(true);
    try {
      const nextPayload = await requestPerformance(params, signal);
      if (request === requestRef.current) {
        setPayload(nextPayload);
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
  }, [gpuUuids, metricQuery, rangeSeconds, selectedNodeCapable, selectedNodeId, visible]);

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
    void requestPerformance(params, controller.signal)
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
      for (const group of visibleGroups) {
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

  function openGuide(metricId: PerformanceMetricId | null, trigger: HTMLElement) {
    guideTriggerRef.current = trigger;
    setGuideMetricId(metricId);
    setGuideOpen(true);
    updateGuideLocation({ open: true, metricId, locale: guideLocale, push: !guideOpen });
  }

  function selectGuideMetric(metricId: PerformanceMetricId) {
    setGuideMetricId(metricId);
    updateGuideLocation({ open: true, metricId, locale: guideLocale, push: false });
  }

  function changeGuideLocale(locale: GuideLocale) {
    setGuideLocale(locale);
    window.localStorage.setItem(GUIDE_LOCALE_KEY, locale);
    updateGuideLocation({ open: true, metricId: guideMetricId, locale, push: false });
  }

  function closeGuide() {
    setGuideOpen(false);
    setGuideMetricId(null);
    if (window.history.state?.constellaGuide) {
      window.history.back();
    } else {
      updateGuideLocation({ open: false, metricId: null, locale: guideLocale, push: false });
    }
    window.requestAnimationFrame(() => guideTriggerRef.current?.focus());
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
          <button
            type="button"
            class="guide-entry"
            onClick={(event) => openGuide(null, event.currentTarget)}
          >Guide</button>
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
        <div class={`performance-groups ${loading && !payload ? "is-loading" : ""}`}>
          {visibleGroups.map((group) => {
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
                          onGuide={(trigger) => openGuide(metric.id, trigger)}
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
      {guideOpen ? (
        <GuideDrawer
          locale={guideLocale}
          activeMetricId={guideMetricId}
          onLocaleChange={changeGuideLocale}
          onMetricSelect={selectGuideMetric}
          onClose={closeGuide}
        />
      ) : null}
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
  onGuide,
}: {
  definition: MetricDefinition;
  series: PerformanceSeries[];
  statsSeries: PerformanceSeries[];
  collapsed: boolean;
  selection: Selection | null;
  onSelection: (selection: Selection) => void;
  onToggle: () => void;
  onGuide: (trigger: HTMLButtonElement) => void;
}) {
  const targetRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const hoveredRef = useRef(false);
  const chartRef = useRef<uPlot | null>(null);
  const resizeRef = useRef<ResizeObserver | null>(null);
  const aligned = useMemo(() => alignSeries(series, definition.id), [definition.id, series]);
  const hasData = aligned.timestamps.length > 0;
  const alignedRef = useRef(aligned);
  const seriesRef = useRef(series);
  alignedRef.current = aligned;
  seriesRef.current = series;

  useEffect(() => {
    if (collapsed || !targetRef.current || !hasData) {
      return;
    }
    const target = targetRef.current;
    const css = getComputedStyle(document.documentElement);
    const chart = new uPlot(
      {
        width: Math.max(320, target.clientWidth),
        height: CHART_HEIGHT,
        padding: [8, 8, 0, 0],
        scales: { x: { time: true }, y: definition.unit === "percent" ? { range: [0, 100] } : {} },
        axes: [
          {
            stroke: css.getPropertyValue("--chart-axis").trim(),
            grid: { stroke: css.getPropertyValue("--chart-grid").trim() },
            splits: fixedTimeSplits,
            values: (_plot, values) => values.map((value) => formatClock(Number(value))),
          },
          {
            size: performanceYAxisSize(definition),
            gap: 8,
            stroke: css.getPropertyValue("--chart-axis").trim(),
            grid: { stroke: css.getPropertyValue("--chart-grid").trim() },
            splits: definition.unit === "percent" ? [0, 20, 40, 60, 80, 100] : undefined,
            values: (_plot, values) => values.map((value) => formatMetricValue(Number(value), definition)),
          },
        ],
        cursor: {
          y: false,
          drag: { x: true, y: false },
          points: { show: false },
          sync: { key: "constella-performance" },
        },
        hooks: {
          setCursor: [
            (plot) => updatePerformanceTooltip(
              plot,
              tooltipRef.current,
              hoveredRef.current,
              alignedRef.current,
              seriesRef.current,
              definition,
            ),
          ],
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
            width: 1.6,
            spanGaps: false,
            paths: uPlot.paths.spline?.(),
            points: { show: false },
            value: (_plot: uPlot, value: number | null | undefined) => formatMetricValue(value, definition),
          })),
        ],
      },
      aligned.data,
      target,
    );
    chartRef.current = chart;
    const resize = new ResizeObserver(([entry]) => {
      chart.setSize({ width: Math.max(320, Math.floor(entry.contentRect.width)), height: CHART_HEIGHT });
    });
    resize.observe(target);
    resizeRef.current = resize;
    return () => {
      resize.disconnect();
      if (tooltipRef.current) {
        resetPerformanceInspector(tooltipRef.current);
      }
      chart.destroy();
      chartRef.current = null;
    };
  }, [collapsed, definition.id, hasData, series.map((item) => item.gpu_uuid).join(",")]);

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
      <div class="performance-chart-head">
        <button type="button" class="performance-chart-toggle" onClick={onToggle}>
          <span><strong>{definition.label}</strong><small>{definition.description}</small></span>
          <em>{collapsed ? "Expand" : "Collapse"}</em>
        </button>
        <button
          type="button"
          class="metric-guide-entry"
          aria-label={`Open guide for ${definition.label}`}
          title={`Open guide for ${definition.label}`}
          onClick={(event) => onGuide(event.currentTarget)}
        ><span aria-hidden="true">i</span></button>
      </div>
      {!collapsed ? (
        <>
          {aligned.timestamps.length ? (
            <>
              <div class="performance-plot-wrap">
                <div
                  ref={targetRef}
                  class="performance-plot uplot-theme"
                  onPointerEnter={() => { hoveredRef.current = true; }}
                  onPointerLeave={() => {
                    hoveredRef.current = false;
                    if (tooltipRef.current) {
                      resetPerformanceInspector(tooltipRef.current);
                    }
                  }}
                />
              </div>
              <div ref={tooltipRef} class="performance-inspector"><strong>Hover chart to inspect exact samples</strong></div>
            </>
          ) : <div class="performance-no-data">No valid samples in this range</div>}
          <div class="performance-summary">
            {statsSeries.map((item, index) => {
              const summary = item.metrics[definition.id]?.summary;
              return (
                <span key={item.gpu_uuid}>
                  <b style={{ background: `var(${COLORS[index % COLORS.length]})` }} />
                  <strong>GPU{item.gpu_index}</strong>
                  <small>Avg {formatMetricValue(summary?.avg, definition)} / Peak {formatMetricValue(summary?.max, definition)} / P95 {formatMetricValue(summary?.p95, definition)} / Coverage {summary?.coverage ?? 0}%</small>
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
    ...series.map((item) => interpolatePoints(item.metrics[metric]?.points || [], timestamps)),
  ];
  return { timestamps, data };
}

function fixedTimeSplits(plot: uPlot, _axisIndex: number, min: number, max: number) {
  const segments = Math.max(2, Math.min(8, Math.floor(plot.width / 180)));
  const step = (max - min) / segments;
  return Array.from({ length: segments - 1 }, (_, index) => min + step * (index + 1));
}

function interpolatePoints(points: [number, number | null][], timestamps: number[]) {
  if (!points.length) {
    return timestamps.map(() => null);
  }
  let pointIndex = 0;
  return timestamps.map((timestamp) => {
    while (pointIndex + 1 < points.length && points[pointIndex + 1][0] <= timestamp) {
      pointIndex += 1;
    }
    const left = points[pointIndex];
    if (left[0] === timestamp) {
      return left[1];
    }
    const right = points[pointIndex + 1];
    if (left[0] > timestamp || !right || left[1] == null || right[1] == null) {
      return null;
    }
    const ratio = (timestamp - left[0]) / (right[0] - left[0]);
    return left[1] + (right[1] - left[1]) * ratio;
  });
}

function updatePerformanceTooltip(
  plot: uPlot,
  tooltip: HTMLDivElement | null,
  hovered: boolean,
  aligned: ReturnType<typeof alignSeries>,
  series: PerformanceSeries[],
  definition: MetricDefinition,
) {
  if (!tooltip || !hovered) {
    return;
  }
  const index = plot.cursor.idx;
  const cursorLeft = plot.cursor.left;
  if (index == null || cursorLeft == null || cursorLeft < 0 || index >= aligned.timestamps.length) {
    resetPerformanceInspector(tooltip);
    return;
  }

  const timestamp = document.createElement("strong");
  timestamp.textContent = formatTooltipTime(aligned.timestamps[index]);
  const rows = series.map((item, seriesIndex) => {
    const row = document.createElement("span");
    const dot = document.createElement("i");
    dot.style.background = `var(${COLORS[seriesIndex % COLORS.length]})`;
    const label = document.createElement("b");
    label.textContent = `GPU${item.gpu_index}`;
    const value = document.createElement("em");
    const point = nearestPointValue(item.metrics[definition.id]?.points || [], aligned.timestamps[index]);
    value.textContent = formatMetricValue(point, definition);
    row.append(dot, label, value);
    return row;
  });
  tooltip.replaceChildren(timestamp, ...rows);
}

function resetPerformanceInspector(inspector: HTMLDivElement) {
  const message = document.createElement("strong");
  message.textContent = "Hover chart to inspect exact samples";
  inspector.replaceChildren(message);
}

function nearestPointValue(points: [number, number | null][], timestamp: number) {
  if (!points.length) {
    return null;
  }
  let low = 0;
  let high = points.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (points[middle][0] < timestamp) {
      low = middle + 1;
    } else {
      high = middle;
    }
  }
  const before = points[Math.max(0, low - 1)];
  const after = points[Math.min(points.length - 1, low)];
  return timestamp - before[0] <= after[0] - timestamp ? before[1] : after[1];
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
  return formatPerformanceTime(timestamp, false);
}

function formatTooltipTime(timestamp: number) {
  return formatPerformanceTime(timestamp, true);
}

function formatPerformanceTime(timestamp: number, includeDate: boolean) {
  return new Intl.DateTimeFormat("en-GB", {
    ...(includeDate ? { month: "short", day: "2-digit" } as const : {}),
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(timestamp * 1000);
}

function formatMetricValue(value: number | null | undefined, definition: MetricDefinition) {
  if (value == null) {
    return "n/a";
  }
  return definition.unit === "mib_per_second" ? fmtMiBPerSecond(value) : fmtPct(value);
}

function performanceYAxisSize(definition: MetricDefinition) {
  return definition.unit === "mib_per_second" ? 92 : 58;
}

async function requestPerformance(params: URLSearchParams, signal?: AbortSignal) {
  const response = await fetch(`/api/highres/performance?${params.toString()}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`performance request failed: ${response.status}`);
  }
  return response.json() as Promise<PerformancePayload>;
}

function profileLabel(node: NodeSnapshot) {
  const profiles = node.performance_profiles || [];
  if (profiles.includes(PROFILE)) {
    return "NVIDIA GPM";
  }
  return profiles.length ? `unsupported: ${profiles.join(", ")}` : "base telemetry";
}

function parseMetricId(value: string | null): PerformanceMetricId | null {
  return value && PERFORMANCE_METRIC_IDS.includes(value as PerformanceMetricId)
    ? value as PerformanceMetricId
    : null;
}

function parseGuideLocale(value: string | null): GuideLocale | null {
  if (value === "zh-CN" || value === "en") {
    return value;
  }
  return null;
}

function readGuideLocale(params: URLSearchParams): GuideLocale {
  const queryLocale = parseGuideLocale(params.get("lang"));
  if (queryLocale) {
    return queryLocale;
  }
  const savedLocale = parseGuideLocale(window.localStorage.getItem(GUIDE_LOCALE_KEY));
  if (savedLocale) {
    return savedLocale;
  }
  return window.navigator.language.toLowerCase().startsWith("zh") ? "zh-CN" : "en";
}

function updateGuideLocation({
  open,
  metricId,
  locale,
  push,
}: {
  open: boolean;
  metricId: PerformanceMetricId | null;
  locale: GuideLocale;
  push: boolean;
}) {
  const params = new URLSearchParams(window.location.search);
  if (open) {
    params.set("guide", "performance");
    params.set("lang", locale);
    if (metricId) {
      params.set("metric", metricId);
    } else {
      params.delete("metric");
    }
  } else {
    params.delete("guide");
    params.delete("metric");
    params.delete("lang");
  }
  const state = { ...(window.history.state || {}) } as Record<string, unknown>;
  if (open) {
    state.constellaGuide = true;
  } else {
    delete state.constellaGuide;
  }
  const query = params.toString();
  const url = `${window.location.pathname}${query ? `?${query}` : ""}`;
  if (push) {
    window.history.pushState(state, "", url);
  } else {
    window.history.replaceState(state, "", url);
  }
}
