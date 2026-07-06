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
  Zap,
  createIcons,
} from "lucide";
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { createAnalyticsController, type Route } from "./analytics";
import { clusterRefreshInterval, findNode, sameInterval } from "./cluster-utils";
import { Fabric, GpuGrid, Header, ProcessSection, Summary } from "./components";
import type { ClusterSnapshot, LiveState, NodeSnapshot, Settings, ThemeMode } from "./types";

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
  Zap,
};

type AnalyticsController = ReturnType<typeof createAnalyticsController>;

const DEFAULT_REFRESH_INTERVALS = [0.5, 1, 2, 5];
const THEME_STORAGE_KEY = "constella.theme";
const COLLAPSE_STORAGE_KEY = "constella.collapsed";

export default function App() {
  const [snapshot, setSnapshot] = useState<ClusterSnapshot | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [currentRefreshInterval, setCurrentRefreshInterval] = useState<number | null>(null);
  const [refreshPending, setRefreshPending] = useState(false);
  const [paused, setPaused] = useState(false);
  const [liveState, setLiveState] = useState<LiveState>("connecting");
  const [route, setRoute] = useState<Route>(() => currentRoute());
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
  const analyticsRef = useRef<AnalyticsController | null>(null);

  pausedRef.current = paused;
  routeRef.current = route;
  settingsRef.current = settings;

  const selectedNode = useMemo(
    () => (snapshot && route.kind === "node" ? findNode(snapshot, route.nodeId) : null),
    [route, snapshot],
  );

  const selectedRefreshInterval = clusterRefreshInterval(snapshot) ?? currentRefreshInterval;
  const displayedLiveState = paused ? "paused" : liveState;

  useEffect(() => {
    if (window.location.pathname === "/") {
      window.history.replaceState(null, "", "/overview");
      setRoute({ kind: "overview" });
    }
    const onPopState = () => setRoute(currentRoute());
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
    const resolved = themeMode === "system" ? (prefersDark ? "dark" : "light") : themeMode;
    document.documentElement.dataset.theme = themeMode;
    document.documentElement.dataset.resolvedTheme = resolved;
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", resolved === "dark" ? "#0f1113" : "#f7f7f4");
    window.localStorage.setItem(THEME_STORAGE_KEY, themeMode);
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
      currentRoute: () => routeRef.current,
      renderIcons: () => createIcons({ icons: iconSet }),
    });
  }, []);

  useEffect(() => {
    const interval = clusterRefreshInterval(snapshot);
    if (typeof interval === "number" && Number.isFinite(interval)) {
      setCurrentRefreshInterval((previous) => (sameInterval(previous, interval) ? previous : interval));
    }
    if (snapshot) {
      setLiveState(snapshot.ok ? "live" : snapshot.totals.node_count ? "error" : "connecting");
    }
  }, [snapshot]);

  useEffect(() => {
    const controller = analyticsRef.current;
    if (!controller) {
      return;
    }
    syncAnalyticsRoute(route);
  }, [route]);

  function syncAnalyticsRoute(nextRoute: Route) {
    const controller = analyticsRef.current;
    if (!controller) {
      return;
    }
    if (nextRoute.kind === "overview") {
      controller.renderOverview();
      createIcons({ icons: iconSet });
      void controller.fetchOverview();
    } else if (nextRoute.kind === "node") {
      controller.renderNode(nextRoute);
      createIcons({ icons: iconSet });
      void controller.fetchNode(nextRoute);
    } else {
      controller.renderJobs();
      createIcons({ icons: iconSet });
      void controller.fetchJobs();
    }
  }

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer = 0;
    let stopped = false;

    const connect = () => {
      window.clearTimeout(reconnectTimer);
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}://${window.location.host}/ws/cluster`);
      setLiveState("connecting");

      socket.addEventListener("open", () => {
        if (!stopped) {
          setLiveState(pausedRef.current ? "paused" : "live");
        }
      });

      socket.addEventListener("message", (event) => {
        const nextSnapshot = JSON.parse(event.data) as ClusterSnapshot;
        latestSnapshotRef.current = nextSnapshot;
        if (!pausedRef.current) {
          setSnapshot(nextSnapshot);
        }
      });

      socket.addEventListener("close", () => {
        if (stopped) {
          return;
        }
        setLiveState("offline");
        reconnectTimer = window.setTimeout(connect, 1200);
      });

      socket.addEventListener("error", () => {
        if (!stopped) {
          setLiveState("offline");
        }
      });
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  useEffect(() => {
    void fetchSettings();
    void fetchSnapshot();
  }, []);

  async function fetchSnapshot() {
    try {
      const response = await fetch("/api/cluster/snapshot", { cache: "no-store" });
      const nextSnapshot = (await response.json()) as ClusterSnapshot;
      latestSnapshotRef.current = nextSnapshot;
      setSnapshot(nextSnapshot);
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
      const nextSettings = (await response.json()) as Settings;
      setSettings(nextSettings);
      setCurrentRefreshInterval(nextSettings.refresh_interval);
    } catch {
      setCurrentRefreshInterval(clusterRefreshInterval(latestSnapshotRef.current) ?? currentRefreshInterval);
    }
  }

  async function setRefreshInterval(interval: number) {
    if (refreshPending || sameInterval(interval, currentRefreshInterval)) {
      return;
    }
    const previous = currentRefreshInterval;
    setRefreshPending(true);
    setCurrentRefreshInterval(interval);
    try {
      const response = await fetch("/api/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_interval: interval }),
      });
      if (!response.ok) {
        throw new Error(`settings update failed: ${response.status}`);
      }
      const nextSettings = (await response.json()) as Settings;
      setSettings(nextSettings);
      setCurrentRefreshInterval(nextSettings.refresh_interval);
    } catch {
      setCurrentRefreshInterval(
        clusterRefreshInterval(latestSnapshotRef.current) ?? settingsRef.current?.refresh_interval ?? previous,
      );
    } finally {
      setRefreshPending(false);
    }
  }

  function navigateTo(pathname: string) {
    const normalized = pathname === "/" ? "/overview" : pathname;
    if (normalized !== window.location.pathname) {
      window.history.pushState(null, "", normalized);
    }
    setRoute(currentRoute());
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
    if (shouldHandleAppLink(event, link)) {
      event.preventDefault();
      navigateTo(link.pathname);
    }
  }

  return (
    <div onClick={handleAppClick}>
      <Header
        snapshot={snapshot}
        route={route}
        selectedNode={selectedNode}
        themeMode={themeMode}
        liveState={displayedLiveState}
        refreshIntervals={settings?.allowed_refresh_intervals || DEFAULT_REFRESH_INTERVALS}
        selectedRefreshInterval={selectedRefreshInterval}
        refreshPending={refreshPending}
        paused={paused}
        onRefreshInterval={setRefreshInterval}
        onTheme={cycleThemeMode}
        onPause={() => setPaused((value) => !value)}
        onRefresh={fetchSnapshot}
      />

      <main class="shell" id="mainContent">
        <section class="summary-grid">
          <Summary snapshot={snapshot} route={route} selectedNode={selectedNode} />
        </section>

        <section class="fabric-band" hidden={route.kind !== "overview"}>
          {snapshot ? <Fabric snapshot={snapshot} /> : <div class="empty-panel">waiting for cluster fabric</div>}
        </section>

        <section class="analytics-section" ref={overviewAnalyticsRef} hidden={route.kind !== "overview"} />

        <section class="analytics-section" ref={jobCurvesRef} hidden={route.kind !== "jobs"} />

        <section class="gpu-grid" hidden={route.kind !== "node"}>
          {route.kind === "node" ? <GpuGrid nodeId={route.nodeId} node={selectedNode} /> : null}
        </section>

        <ProcessSection
          hidden={route.kind !== "node"}
          nodeId={route.kind === "node" ? route.nodeId : ""}
          node={selectedNode}
          collapsed={collapsedSections.has("processes")}
        />

        <section class="analytics-section" ref={nodeHistoryRef} hidden={route.kind !== "node"} />
      </main>
    </div>
  );
}

function currentRoute(): Route {
  const path = window.location.pathname.replace(/\/+$/, "") || "/overview";
  if (path.startsWith("/nodes/")) {
    const encoded = path.slice("/nodes/".length);
    return { kind: "node", nodeId: decodeURIComponent(encoded) };
  }
  if (path === "/jobs") {
    return { kind: "jobs" };
  }
  return { kind: "overview" };
}

function isAppPath(pathname: string) {
  return pathname === "/" || pathname === "/overview" || pathname === "/jobs" || pathname.startsWith("/nodes/");
}

function shouldHandleAppLink(event: JSX.TargetedMouseEvent<HTMLDivElement>, link: HTMLAnchorElement | null): link is HTMLAnchorElement {
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

function readThemeMode(): ThemeMode {
  const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
  return saved === "light" || saved === "dark" || saved === "system" ? saved : "system";
}

function readCollapsedSections() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(COLLAPSE_STORAGE_KEY) || "[]");
    return new Set(Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : []);
  } catch {
    return new Set<string>();
  }
}
