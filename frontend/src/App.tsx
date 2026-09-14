import {
  Activity,
  AlertTriangle,
  BarChart3,
  Clock3,
  ChevronDown,
  Cpu,
  Database,
  Gauge,
  LineChart,
  ListTree,
  Maximize2,
  Monitor,
  Moon,
  Pause,
  Play,
  RefreshCw,
  Search,
  Server,
  Sun,
  Table2,
  Thermometer,
  Users,
  X,
  Zap,
  createIcons,
} from "lucide";
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { createAnalyticsController, type Route } from "./analytics";
import type { AppExtension, AppRoute } from "./app-extension";
import { clusterRefreshInterval, findNode, sameInterval } from "./cluster-utils";
import { connectLive } from "./live-connection";
import { fetchJson, RequestError } from "./requests";
import { Fabric, GpuGrid, Header, ProcessSection, Summary } from "./components";
import { PerformancePage } from "./performance";
import { applyDocumentTheme, readThemeMode } from "./theme";
import type { ClusterSnapshot, LiveState, Settings, ThemeMode } from "./types";

const iconSet = {
  Activity,
  AlertTriangle,
  BarChart3,
  Clock3,
  ChevronDown,
  Cpu,
  Database,
  Gauge,
  LineChart,
  ListTree,
  Maximize2,
  Monitor,
  Moon,
  Pause,
  Play,
  RefreshCw,
  Search,
  Server,
  Sun,
  Table2,
  Thermometer,
  Users,
  X,
  Zap,
};

type AnalyticsController = ReturnType<typeof createAnalyticsController>;

const DEFAULT_REFRESH_INTERVALS = [0.5, 1, 2, 5];
const COLLAPSE_STORAGE_KEY = "constella.collapsed";

export default function App({ extension }: { extension?: AppExtension }) {
  const [snapshot, setSnapshot] = useState<ClusterSnapshot | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [currentRefreshInterval, setCurrentRefreshInterval] = useState<number | null>(null);
  const [refreshPending, setRefreshPending] = useState(false);
  const [paused, setPaused] = useState(false);
  const [liveState, setLiveState] = useState<LiveState>("connecting");
  const [route, setRoute] = useState<AppRoute>(() => currentRoute(extension));
  const [themeMode, setThemeModeState] = useState<ThemeMode>(() => readThemeMode());
  const [prefersDark, setPrefersDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches);
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(() => readCollapsedSections());

  const pausedRef = useRef(paused);
  const latestSnapshotRef = useRef<ClusterSnapshot | null>(null);
  const settingsRef = useRef<Settings | null>(null);
  const routeRef = useRef(route);
  const overviewAnalyticsRef = useRef<HTMLElement>(null);
  const nodeHistoryRef = useRef<HTMLElement>(null);
  const jobCurvesRef = useRef<HTMLElement>(null);
  const liveRef = useRef<ReturnType<typeof connectLive> | null>(null);
  const snapshotRequest = useRef<AbortController | null>(null);
  const analyticsRef = useRef<AnalyticsController | null>(null);

  pausedRef.current = paused;
  routeRef.current = route;
  settingsRef.current = settings;

  const selectedNode = useMemo(
    () => (snapshot && route.kind === "node" ? findNode(snapshot, route.nodeId) : null),
    [route, snapshot],
  );

  const selectedRefreshInterval = clusterRefreshInterval(snapshot) ?? currentRefreshInterval;
  const displayedLiveState = paused ? "paused"
    : liveState === "live" && snapshot && !snapshot.ok && snapshot.totals.node_count ? "error" : liveState;

  useEffect(() => {
    if (window.location.pathname === "/") {
      window.history.replaceState(null, "", "/overview");
      setRoute({ kind: "overview" });
    }
    const onPopState = () => setRoute(currentRoute(extension));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setPrefersDark(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    applyDocumentTheme(themeMode, prefersDark);
  }, [prefersDark, themeMode]);

  useEffect(() => {
    createIcons({ icons: iconSet });
  });

  useEffect(() => {
    if (!overviewAnalyticsRef.current || !nodeHistoryRef.current || !jobCurvesRef.current || analyticsRef.current) {
      return;
    }
    analyticsRef.current = createAnalyticsController({
      overviewElement: overviewAnalyticsRef.current,
      nodeElement: nodeHistoryRef.current,
      jobElement: jobCurvesRef.current,
      currentRoute: () => coreRoute(routeRef.current),
      requestHeaders: extension?.requestHeaders,
      onAuthenticationRequired: extension?.onAuthenticationRequired,
      renderIcons: () => createIcons({ icons: iconSet }),
    });
    return () => { analyticsRef.current?.dispose(); analyticsRef.current = null; };
  }, []);

  useEffect(() => {
    const interval = clusterRefreshInterval(snapshot);
    if (typeof interval === "number" && Number.isFinite(interval)) {
      setCurrentRefreshInterval((previous) => (sameInterval(previous, interval) ? previous : interval));
    }
  }, [snapshot]);

  useEffect(() => {
    const controller = analyticsRef.current;
    if (!controller) {
      return;
    }
    syncAnalyticsRoute(route);
  }, [route]);

  function syncAnalyticsRoute(nextRoute: AppRoute, force = false) {
    const controller = analyticsRef.current;
    if (!controller) {
      return;
    }
    if (nextRoute.kind === "overview") {
      controller.renderOverview();
      createIcons({ icons: iconSet });
      void controller.fetchOverview(force);
    } else if (nextRoute.kind === "node") {
      controller.renderNode(nextRoute);
      createIcons({ icons: iconSet });
      void controller.fetchNode(nextRoute);
    } else if (nextRoute.kind === "jobs") {
      controller.syncJobsLocation(window.location.search);
      controller.renderJobs();
      createIcons({ icons: iconSet });
      void controller.fetchJobs();
    }
  }

  useEffect(() => {
    const connection = connectLive({
      message: (data) => {
        const nextSnapshot = JSON.parse(data) as ClusterSnapshot;
        latestSnapshotRef.current = nextSnapshot;
        if (!pausedRef.current) setSnapshot(nextSnapshot);
      },
      state: setLiveState,
      interval: () => settingsRef.current?.refresh_interval ?? 1,
      recover: fetchSnapshot,
      refresh: () => {
        const current = coreRoute(routeRef.current);
        if (current.kind === "node") void analyticsRef.current?.fetchNode(current, true);
        else syncAnalyticsRoute(routeRef.current, true);
      },
    });
    liveRef.current = connection;
    void fetchSettings();
    void fetchSnapshot().catch(() => {});
    return () => { connection.dispose(); snapshotRequest.current?.abort(); };
  }, []);

  async function fetchSnapshot(manual = false): Promise<boolean> {
    snapshotRequest.current?.abort();
    const request = new AbortController();
    snapshotRequest.current = request;
    const previous = latestSnapshotRef.current;
    try {
      const nextSnapshot = await fetchJson<ClusterSnapshot>("/api/cluster/snapshot", {
        headers: extension?.requestHeaders,
        signal: request.signal,
      });
      if (request.signal.aborted || snapshotRequest.current !== request) return true;
      if (latestSnapshotRef.current !== previous) {
        if (manual) setSnapshot(latestSnapshotRef.current);
        return true;
      }
      latestSnapshotRef.current = nextSnapshot;
      if (manual || !pausedRef.current) setSnapshot(nextSnapshot);
      return true;
    } catch (error) {
      if (request.signal.aborted) return true;
      if (error instanceof RequestError && error.status === 401) {
        extension?.onAuthenticationRequired?.();
        return false;
      }
      throw error;
    }
  }

  async function fetchSettings() {
    try {
      const nextSettings = await fetchJson<Settings>("/api/settings", {
        headers: extension?.requestHeaders,
      });
      setSettings(nextSettings);
      setCurrentRefreshInterval(nextSettings.refresh_interval);
    } catch (error) {
      if (error instanceof RequestError && error.status === 401) extension?.onAuthenticationRequired?.();
      setCurrentRefreshInterval(clusterRefreshInterval(latestSnapshotRef.current) ?? currentRefreshInterval);
    }
  }

  async function setRefreshInterval(interval: number) {
    if (
      refreshPending ||
      extension?.canManageSettings === false ||
      sameInterval(interval, currentRefreshInterval)
    ) {
      return;
    }
    const previous = currentRefreshInterval;
    setRefreshPending(true);
    setCurrentRefreshInterval(interval);
    try {
      const nextSettings = await fetchJson<Settings>("/api/settings", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...extension?.requestHeaders,
          "X-Constella-Request": "same-origin",
        },
        body: JSON.stringify({ refresh_interval: interval }),
      });
      setSettings(nextSettings);
      setCurrentRefreshInterval(nextSettings.refresh_interval);
    } catch (error) {
      if (error instanceof RequestError && error.status === 401) extension?.onAuthenticationRequired?.();
      setCurrentRefreshInterval(
        clusterRefreshInterval(latestSnapshotRef.current) ?? settingsRef.current?.refresh_interval ?? previous,
      );
    } finally {
      setRefreshPending(false);
    }
  }

  function navigateTo(href: string) {
    const target = new URL(href, window.location.origin);
    const pathname = target.pathname === "/" ? "/overview" : target.pathname;
    const nextLocation = `${pathname}${target.search}`;
    const currentLocation = `${window.location.pathname}${window.location.search}`;
    if (nextLocation !== currentLocation) {
      window.history.pushState(null, "", nextLocation);
    }
    setRoute(currentRoute(extension));
  }

  function cycleThemeMode() {
    const modes: ThemeMode[] = ["system", "light", "dark"];
    setThemeModeState(modes[(modes.indexOf(themeMode) + 1) % modes.length]);
  }

  function toggleSection(section: string) {
    if (!section) {
      return;
    }
    setCollapsedSections((previous) => {
      const next = new Set(previous);
      if (next.has(section)) {
        next.delete(section);
      } else {
        next.add(section);
      }
      window.localStorage.setItem(COLLAPSE_STORAGE_KEY, JSON.stringify(Array.from(next)));
      return next;
    });
  }

  function handleAppClick(event: JSX.TargetedMouseEvent<HTMLDivElement>) {
    const targetElement = event.target as HTMLElement;
    const collapseTarget = targetElement.closest("[data-collapse-target]") as HTMLButtonElement | null;
    if (collapseTarget) {
      event.preventDefault();
      toggleSection(collapseTarget.dataset.collapseTarget || "");
      return;
    }

    const analyticsTarget = targetElement.closest("[data-analytics-action]") as HTMLButtonElement | null;
    if (analyticsTarget && !analyticsTarget.disabled && analyticsRef.current?.handleClick(analyticsTarget)) {
      event.preventDefault();
      return;
    }

    const link = targetElement.closest("a[href]") as HTMLAnchorElement | null;
    if (shouldHandleAppLink(event, link, extension)) {
      event.preventDefault();
      navigateTo(`${link.pathname}${link.search}`);
    }
  }

  function handleAppKeyDown(event: JSX.TargetedKeyboardEvent<HTMLDivElement>) {
    if (analyticsRef.current?.handleKeyDown(event as unknown as KeyboardEvent)) {
      return;
    }
  }

  function handleAppSubmit(event: JSX.TargetedEvent<HTMLDivElement, SubmitEvent>) {
    const target = event.target as HTMLFormElement;
    if (analyticsRef.current?.handleSubmit(target)) {
      event.preventDefault();
    }
  }

  function handleAppChange(event: JSX.TargetedEvent<HTMLDivElement, Event>) {
    const target = event.target as HTMLElement;
    const select = target.closest("select[data-job-metric]") as HTMLSelectElement | null;
    if (select) {
      analyticsRef.current?.handleChange(select);
    }
  }

  return (
    <div onClick={handleAppClick} onChange={handleAppChange} onKeyDown={handleAppKeyDown} onSubmit={handleAppSubmit}>
      <Header
        snapshot={snapshot}
        route={route}
        selectedNode={selectedNode}
        themeMode={themeMode}
        liveState={displayedLiveState}
        refreshIntervals={settings?.allowed_refresh_intervals || DEFAULT_REFRESH_INTERVALS}
        selectedRefreshInterval={selectedRefreshInterval}
        refreshPending={refreshPending}
        canManageSettings={extension?.canManageSettings !== false}
        paused={paused}
        onRefreshInterval={setRefreshInterval}
        onTheme={cycleThemeMode}
        onPause={() => setPaused((value) => !value)}
        onRefresh={() => liveRef.current?.recover()}
        extraNavigation={extension?.renderNavigation(route)}
        extraActions={extension?.renderHeaderActions()}
      />

      <main class="shell" id="mainContent">
        <section class="summary-grid" hidden={route.kind === "performance" || route.kind === "extension"}>
          <Summary snapshot={snapshot} route={coreRoute(route)} selectedNode={selectedNode} />
        </section>

        <PerformancePage snapshot={snapshot} visible={route.kind === "performance"} />

        <section class="fabric-band" hidden={route.kind !== "overview"}>
          {snapshot ? <Fabric snapshot={snapshot} /> : <div class="empty-panel">waiting for cluster fabric</div>}
        </section>

        <section class="analytics-section" ref={overviewAnalyticsRef} hidden={route.kind !== "overview"} />

        <section class="analytics-section jobs-section" ref={jobCurvesRef} hidden={route.kind !== "jobs"} />

        <section class="gpu-grid" hidden={route.kind !== "node"}>
          {route.kind === "node" ? <GpuGrid nodeId={route.nodeId} node={selectedNode} /> : null}
        </section>

        <ProcessSection
          hidden={route.kind !== "node"}
          nodeId={route.kind === "node" ? route.nodeId : ""}
          node={selectedNode}
          showDetails={extension?.showNodeProcessDetails ?? true}
          collapsed={collapsedSections.has("processes")}
        />

        <section class="analytics-section" ref={nodeHistoryRef} hidden={route.kind !== "node"} />

        {route.kind === "extension" ? (
          <section class="lab-page">{extension?.renderPage(route, snapshot)}</section>
        ) : null}
      </main>
    </div>
  );
}

function currentRoute(extension?: AppExtension): AppRoute {
  const path = window.location.pathname.replace(/\/+$/, "") || "/overview";
  const extensionRoute = extension?.parseRoute(path);
  if (extensionRoute) {
    return extensionRoute;
  }
  if (path.startsWith("/nodes/")) {
    const encoded = path.slice("/nodes/".length);
    return { kind: "node", nodeId: decodeURIComponent(encoded) };
  }
  if (path === "/jobs") {
    return { kind: "jobs" };
  }
  if (path === "/performance") {
    return { kind: "performance" };
  }
  return { kind: "overview" };
}

function isAppPath(pathname: string, extension?: AppExtension) {
  return extension?.isPath(pathname) === true || pathname === "/" || pathname === "/overview" || pathname === "/jobs" || pathname === "/performance" || pathname.startsWith("/nodes/");
}

function shouldHandleAppLink(event: JSX.TargetedMouseEvent<HTMLDivElement>, link: HTMLAnchorElement | null, extension?: AppExtension): link is HTMLAnchorElement {
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
  if (!link || link.origin !== window.location.origin || !isAppPath(link.pathname, extension)) {
    return false;
  }
  return !link.target && !link.hasAttribute("download");
}

function coreRoute(route: AppRoute): Route {
  return route.kind === "extension" ? { kind: "overview" } : route;
}

function readCollapsedSections() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(COLLAPSE_STORAGE_KEY) || "[]");
    return new Set(Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : []);
  } catch {
    return new Set<string>();
  }
}
