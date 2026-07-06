import { useMemo } from "preact/hooks";
import type { Route } from "./analytics";
import {
  clamp,
  compactGpuName,
  fabricConfigItems,
  fabricConfigSummary,
  fabricNodeSizeClass,
  fmtLatency,
  formatInterval,
  maxClusterLatency,
  nodeHealthPercent,
  sameInterval,
  statusClass,
  tempClass,
} from "./cluster-utils";
import { fmtDuration, fmtGiB, fmtPct } from "./format";
import type { ClusterSnapshot, GpuInfo, LiveState, NodeSnapshot, ThemeMode } from "./types";

export type HeaderProps = {
  snapshot: ClusterSnapshot | null;
  route: Route;
  selectedNode: NodeSnapshot | null;
  themeMode: ThemeMode;
  liveState: LiveState;
  refreshIntervals: number[];
  selectedRefreshInterval: number | null;
  refreshPending: boolean;
  paused: boolean;
  onRefreshInterval: (interval: number) => void;
  onTheme: () => void;
  onPause: () => void;
  onRefresh: () => void;
};

export function Header({
  snapshot,
  route,
  selectedNode,
  themeMode,
  liveState,
  refreshIntervals,
  selectedRefreshInterval,
  refreshPending,
  paused,
  onRefreshInterval,
  onTheme,
  onPause,
  onRefresh,
}: HeaderProps) {
  const themeIcon = themeMode === "system" ? "monitor" : themeMode === "dark" ? "moon" : "sun";
  const pauseTitle = paused ? "Resume stream" : "Pause stream";
  return (
    <header class="topbar">
      <div class="brand">
        <a class="brand-mark" href="/overview" aria-label="Constella overview">
          <img class="brand-logo" src="/logo.svg?v=20260627" alt="" />
        </a>
        <div>
          <h1>
            <a href="/overview">Constella</a>
          </h1>
          <p>{headerLine(snapshot, route, selectedNode)}</p>
        </div>
      </div>

      <Nav snapshot={snapshot} route={route} />

      <div class="status-cluster">
        <RefreshControl
          intervals={refreshIntervals}
          selected={selectedRefreshInterval}
          disabled={refreshPending}
          onSelect={onRefreshInterval}
        />
        <button
          class="icon-button theme-button"
          type="button"
          aria-label={`Theme: ${themeMode}`}
          title={`Theme: ${themeMode}`}
          onClick={onTheme}
        >
          <Icon name={themeIcon} />
        </button>
        <span class={`live-pill is-${liveState}`}>
          <span />
          {liveState}
        </span>
        <button class="icon-button" type="button" aria-label={pauseTitle} title={pauseTitle} onClick={onPause}>
          <Icon name={paused ? "play" : "pause"} />
        </button>
        <button
          class="icon-button"
          type="button"
          aria-label="Refresh snapshot"
          title="Refresh snapshot"
          onClick={onRefresh}
        >
          <Icon name="refresh-cw" />
        </button>
      </div>
    </header>
  );
}

export function RefreshControl({
  intervals,
  selected,
  disabled,
  onSelect,
}: {
  intervals: number[];
  selected: number | null;
  disabled: boolean;
  onSelect: (interval: number) => void;
}) {
  return (
    <div class="refresh-control" role="group" aria-label="Refresh interval">
      {intervals
        .filter((interval) => Number.isFinite(interval) && interval > 0)
        .map((interval) => {
          const active = sameInterval(interval, selected);
          return (
            <button
              key={interval}
              class={`refresh-option ${active ? "is-active" : ""}`}
              type="button"
              data-refresh-interval={interval}
              aria-pressed={active ? "true" : "false"}
              disabled={disabled}
              onClick={() => onSelect(interval)}
            >
              {formatInterval(interval)}
            </button>
          );
        })}
    </div>
  );
}

export function Nav({ snapshot, route }: { snapshot: ClusterSnapshot | null; route: Route }) {
  const overviewActive = route.kind === "overview";
  const jobsActive = route.kind === "jobs";
  return (
    <nav class="top-nav" aria-label="Primary navigation">
      <div class="nav-row nav-row-primary">
        <a class={`nav-link ${overviewActive ? "is-active" : ""}`} aria-current={overviewActive ? "page" : undefined} href="/overview">
          <Icon name="list-tree" />
          <span>Overview</span>
        </a>
        <a class={`nav-link ${jobsActive ? "is-active" : ""}`} aria-current={jobsActive ? "page" : undefined} href="/jobs">
          <Icon name="line-chart" />
          <span>Jobs</span>
        </a>
      </div>
      <div class="nav-row nav-row-nodes" aria-label="Nodes">
        {(snapshot?.nodes || []).map((node) => {
          const active = route.kind === "node" && (route.nodeId === node.node_id || route.nodeId === node.hostname);
          return (
            <a
              key={node.node_id}
              class={`nav-link node-link ${active ? "is-active" : ""}`}
              aria-current={active ? "page" : undefined}
              href={`/nodes/${encodeURIComponent(node.node_id)}`}
            >
              <Icon name="server" />
              <span>{node.node_id}</span>
            </a>
          );
        })}
      </div>
    </nav>
  );
}

export function Summary({
  snapshot,
  route,
  selectedNode,
}: {
  snapshot: ClusterSnapshot | null;
  route: Route;
  selectedNode: NodeSnapshot | null;
}) {
  if (!snapshot) {
    return (
      <>
        <SkeletonMetric label="Nodes" value="waiting" meta="cluster snapshot" />
        <SkeletonMetric label="GPU Avg" value="waiting" meta="realtime stream" />
        <SkeletonMetric label="Memory" value="waiting" meta="aggregate usage" />
        <SkeletonMetric label="Power" value="waiting" meta="cluster draw" />
        <SkeletonMetric label="Tasks" value="waiting" meta="active workloads" />
      </>
    );
  }
  if (route.kind === "node") {
    return <NodeSummary nodeId={route.nodeId} node={selectedNode} />;
  }
  const totals = snapshot.totals;
  return (
    <>
      <MetricCard
        iconName="server"
        label="Nodes"
        value={`${totals.online_node_count} / ${totals.node_count}`}
        meta={`${totals.stale_node_count} stale / ${totals.offline_node_count} offline`}
        percent={nodeHealthPercent(totals)}
        tone="green"
      />
      <MetricCard
        iconName="activity"
        label="GPU Avg"
        value={fmtPct(totals.avg_gpu_utilization)}
        meta={`${totals.gpu_count} GPUs`}
        percent={totals.avg_gpu_utilization}
        tone="cyan"
      />
      <MetricCard
        iconName="database"
        label="Memory Used"
        value={`${fmtGiB(totals.memory_used_mb)} / ${fmtGiB(totals.memory_total_mb)}`}
        meta={fmtPct(totals.avg_memory_utilization)}
        percent={totals.avg_memory_utilization}
        tone="violet"
      />
      <MetricCard
        iconName="zap"
        label="Power"
        value={`${totals.power_watts.toFixed(0)} W / ${totals.power_limit_watts.toFixed(0)} W`}
        meta={totals.power_limit_watts ? fmtPct((totals.power_watts / totals.power_limit_watts) * 100) : "n/a"}
        percent={totals.power_limit_watts ? (totals.power_watts / totals.power_limit_watts) * 100 : 0}
        tone="amber"
      />
      <MetricCard
        iconName="users"
        label="Tasks"
        value={`${totals.active_processes}`}
        meta={`max ${totals.max_temperature_c}°C`}
        percent={Math.min(100, (totals.active_processes / Math.max(1, totals.gpu_count * 4)) * 100)}
        tone="red"
      />
    </>
  );
}

export function NodeSummary({ nodeId, node }: { nodeId: string; node: NodeSnapshot | null }) {
  if (!node) {
    return (
      <>
        <MetricCard iconName="server" label="Node" value={nodeId} meta="not found" percent={0} tone="red" />
        <MetricCard iconName="activity" label="GPU Avg" value="n/a" meta="0 GPUs" percent={0} tone="cyan" />
        <MetricCard iconName="database" label="Memory Used" value="n/a" meta="n/a" percent={0} tone="violet" />
        <MetricCard iconName="zap" label="Power" value="n/a" meta="n/a" percent={0} tone="amber" />
        <MetricCard iconName="users" label="Tasks" value="0" meta="no active tasks" percent={0} tone="red" />
      </>
    );
  }
  const totals = node.totals;
  return (
    <>
      <MetricCard
        iconName="server"
        label="Node"
        value={node.node_id}
        meta={`${node.status} / ${node.hostname}`}
        percent={node.status === "online" ? 100 : 0}
        tone={node.status === "online" ? "green" : "red"}
      />
      <MetricCard
        iconName="activity"
        label="GPU Avg"
        value={fmtPct(totals.avg_gpu_utilization)}
        meta={`${totals.gpu_count} GPUs`}
        percent={totals.avg_gpu_utilization}
        tone="cyan"
      />
      <MetricCard
        iconName="database"
        label="Memory Used"
        value={`${fmtGiB(totals.memory_used_mb)} / ${fmtGiB(totals.memory_total_mb)}`}
        meta={fmtPct(totals.avg_memory_utilization)}
        percent={totals.avg_memory_utilization}
        tone="violet"
      />
      <MetricCard
        iconName="zap"
        label="Power"
        value={`${totals.power_watts.toFixed(0)} W / ${totals.power_limit_watts.toFixed(0)} W`}
        meta={totals.power_limit_watts ? fmtPct((totals.power_watts / totals.power_limit_watts) * 100) : "n/a"}
        percent={totals.power_limit_watts ? (totals.power_watts / totals.power_limit_watts) * 100 : 0}
        tone="amber"
      />
      <MetricCard
        iconName="users"
        label="Tasks"
        value={`${totals.active_processes}`}
        meta={`max ${totals.max_temperature_c}°C`}
        percent={Math.min(100, (totals.active_processes / Math.max(1, totals.gpu_count * 4)) * 100)}
        tone="red"
      />
    </>
  );
}

export function SkeletonMetric({ label, value, meta }: { label: string; value: string; meta: string }) {
  return (
    <article class="metric-card skeleton-card">
      <div class="metric-icon" />
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{meta}</small>
      </div>
    </article>
  );
}

export function MetricCard({
  iconName,
  label,
  value,
  meta,
  percent,
  tone,
}: {
  iconName: string;
  label: string;
  value: string;
  meta: string;
  percent: number;
  tone: string;
}) {
  return (
    <article class={`metric-card tone-${tone}`}>
      <div class="metric-icon">
        <Icon name={iconName} />
      </div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{meta}</small>
      </div>
      <div class="metric-rail">
        <span style={{ width: `${clamp(percent)}%` }} />
      </div>
    </article>
  );
}

export function Fabric({ snapshot }: { snapshot: ClusterSnapshot }) {
  const configItems = fabricConfigItems(snapshot);
  return (
    <>
      <div class="fabric-copy">
        <div class="fabric-config">
          <div class="fabric-title">
            <span class="fabric-kicker">Cluster fabric</span>
            <strong>{fabricConfigSummary(snapshot, configItems)}</strong>
          </div>
          <div class="fabric-config-chips">
            {configItems.length ? (
              configItems.map((item) => (
                <span class="fabric-config-chip" key={`${item.name}:${item.architecture || ""}`}>
                  <b>{item.count} ×</b>
                  <span>
                    <strong>{item.name}</strong>
                    {item.architecture ? <small>{item.architecture}</small> : null}
                  </span>
                </span>
              ))
            ) : (
              <span class="fabric-config-empty">waiting for GPU inventory</span>
            )}
          </div>
        </div>
        <div class="fabric-stats">
          <span>
            {snapshot.totals.online_node_count}/{snapshot.totals.node_count} online
          </span>
          <span>{snapshot.totals.gpu_count} GPUs</span>
          <span>{fmtGiB(snapshot.totals.memory_total_mb)} Memory total</span>
        </div>
      </div>
      <div class="fabric-node-grid">
        {snapshot.nodes.length ? (
          snapshot.nodes.map((node) => <FabricNodeCard key={node.node_id} node={node} />)
        ) : (
          <div class="empty-panel">no nodes</div>
        )}
      </div>
    </>
  );
}

export function FabricNodeCard({ node }: { node: NodeSnapshot }) {
  return (
    <a
      class={`fabric-node-card is-${node.status} ${fabricNodeSizeClass(node)}`}
      href={`/nodes/${encodeURIComponent(node.node_id)}`}
      title={node.error || node.hostname}
    >
      <div class="fabric-node-head">
        <div>
          <span>{node.node_id}</span>
          <strong>{node.hostname}</strong>
        </div>
        <em>{node.status}</em>
      </div>
      <div class="fabric-node-meta">
        {node.totals.gpu_count} GPUs / {fmtPct(node.totals.avg_gpu_utilization)} avg / {fmtLatency(node)}
      </div>
      <div class="fabric-node-gpus">
        {node.gpus.length ? (
          node.gpus.map((gpu) => (
            <div class={`fabric-chip ${statusClass(gpu.utilization_gpu)}`} title={`${node.node_id} GPU${gpu.index}`} key={gpu.uuid}>
              <span>GPU{gpu.index}</span>
              <strong>{Math.round(gpu.utilization_gpu)}%</strong>
              <small>{fmtGiB(gpu.memory_used_mb)}</small>
            </div>
          ))
        ) : (
          <span class="fabric-empty">no GPUs</span>
        )}
      </div>
    </a>
  );
}

export function GpuGrid({ nodeId, node }: { nodeId: string; node: NodeSnapshot | null }) {
  if (!node) {
    return <div class="empty-panel">Node {nodeId} not found</div>;
  }
  if (!node.gpus.length) {
    return <div class="empty-panel">{node.error || "No GPU snapshot available"}</div>;
  }
  return (
    <>
      {node.gpus.map((gpu) => (
        <GpuCard
          key={gpu.uuid}
          node={node}
          gpu={gpu}
          history={node.history[gpu.gpu_id || `${node.node_id}:${gpu.uuid}`] || {}}
        />
      ))}
    </>
  );
}

export function GpuCard({
  node,
  gpu,
  history,
}: {
  node: NodeSnapshot;
  gpu: GpuInfo;
  history: Record<string, number[]>;
}) {
  const subtitle = [
    node.node_id,
    gpu.pstate,
    gpu.compute_mode,
    gpu.mig_mode ? `MIG ${gpu.mig_mode}` : null,
    gpu.ecc_mode ? `ECC ${gpu.ecc_mode}` : null,
  ]
    .filter(Boolean)
    .join(" / ");
  const smClock = gpu.clock_sm_mhz ? `SM ${gpu.clock_sm_mhz} MHz` : "SM clock n/a";
  const memClock = gpu.clock_mem_mhz ? `MEM ${gpu.clock_mem_mhz} MHz` : "MEM clock n/a";
  return (
    <article class="gpu-card">
      <div class="gpu-head">
        <div>
          <span class="gpu-index" title={gpu.uuid}>
            GPU{gpu.index}
          </span>
          <h3>{compactGpuName(gpu.name)}</h3>
          <p>{subtitle || gpu.uuid}</p>
        </div>
        <div class={`temp-badge ${tempClass(gpu.temperature_c)}`}>{gpu.temperature_c}°C</div>
      </div>

      <div class="spark-wrap">
        <Sparkline values={history.gpu || []} color="var(--accent)" max={100} />
      </div>

      <div class="bar-stack">
        <Bar label="GPU" value={gpu.utilization_gpu} meta={fmtPct(gpu.utilization_gpu)} tone="green" />
        <Bar
          label="Memory"
          value={gpu.memory_percent}
          meta={`${fmtGiB(gpu.memory_used_mb)} / ${fmtGiB(gpu.memory_total_mb)}`}
          tone="cyan"
        />
        <Bar
          label="Power"
          value={gpu.power_percent}
          meta={`${gpu.power_watts.toFixed(0)} / ${gpu.power_limit_watts.toFixed(0)} W`}
          tone="amber"
        />
      </div>

      <div class="mini-stats">
        <span>
          <Icon name="gauge" />
          {fmtPct(gpu.utilization_mem)} mem util
        </span>
        <span>
          <Icon name="clock-3" />
          {smClock}
        </span>
        <span>
          <Icon name="server" />
          {node.status} / {fmtLatency(node)}
        </span>
        <span>
          <Icon name="cpu" />
          {memClock}
        </span>
      </div>
    </article>
  );
}

export function Bar({ label, value, meta, tone }: { label: string; value: number; meta: string; tone: string }) {
  return (
    <div class={`bar-row tone-${tone}`}>
      <div class="bar-label">
        <span>{label}</span>
        <strong>{meta}</strong>
      </div>
      <div class="bar-track">
        <span style={{ width: `${clamp(value)}%` }} />
      </div>
    </div>
  );
}

export function Sparkline({ values, color, max }: { values: number[]; color: string; max: number }) {
  const width = 180;
  const height = 46;
  const points = values
    .map((value, index) => {
      const x = (index / Math.max(1, values.length - 1)) * width;
      const y = height - (clamp(value) / max) * (height - 6) - 3;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg class="spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label="GPU history">
      {values.length >= 2 ? <polyline points={points} style={{ stroke: color }} /> : null}
    </svg>
  );
}

export function ProcessSection({
  hidden,
  nodeId,
  node,
  collapsed,
}: {
  hidden: boolean;
  nodeId: string;
  node: NodeSnapshot | null;
  collapsed: boolean;
}) {
  const rows = useMemo(() => processRows(node), [node]);
  return (
    <section
      class={`process-section collapsible-section ${collapsed ? "is-collapsed" : ""}`}
      data-collapse-section="processes"
      hidden={hidden}
    >
      <div class="section-head">
        <div>
          <h2>Active GPU tasks</h2>
          <span>
            {node?.node_id || nodeId} / {rows.length} active
          </span>
        </div>
        <button
          class="section-toggle"
          type="button"
          data-collapse-target="processes"
          aria-expanded={collapsed ? "false" : "true"}
          aria-controls="processTablePanel"
        >
          {collapsed ? "Expand" : "Collapse"}
        </button>
      </div>
      <div class="process-table-wrap" id="processTablePanel" data-collapse-panel hidden={collapsed}>
        <table class="process-table">
          <thead>
            <tr>
              <th>Node</th>
              <th>GPU</th>
              <th>User</th>
              <th>PID</th>
              <th>Task</th>
              <th>Memory</th>
              <th>Runtime</th>
              <th>Type</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.slice(0, 80).map((row) => (
                <tr title={row.title} key={`${row.node}:${row.gpu}:${row.pid}:${row.task}`}>
                  <td>{row.node}</td>
                  <td>
                    <span class="gpu-pill">GPU{row.gpu}</span>
                  </td>
                  <td>{row.user}</td>
                  <td>{row.pid}</td>
                  <td>{row.task}</td>
                  <td>{fmtGiB(row.memory)}</td>
                  <td>{fmtDuration(row.runtime)}</td>
                  <td>{row.kind}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colspan={8} class="empty">
                  no active GPU tasks
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export type ProcessRow = {
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

export function processRows(node: NodeSnapshot | null) {
  const rows: ProcessRow[] = [];
  if (!node) {
    return rows;
  }
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
  return rows.sort(
    (a, b) => a.node.localeCompare(b.node) || a.gpu - b.gpu || b.memory - a.memory || (b.runtime || 0) - (a.runtime || 0),
  );
}

export function Icon({ name }: { name: string }) {
  return <i data-lucide={name} />;
}

function headerLine(snapshot: ClusterSnapshot | null, route: Route, selectedNode: NodeSnapshot | null) {
  if (!snapshot) {
    return "waiting for snapshot";
  }
  const totals = snapshot.totals;
  if (route.kind === "node") {
    return selectedNode
      ? `${selectedNode.node_id} / ${selectedNode.status} / ${selectedNode.totals.gpu_count} GPUs / ${fmtLatency(selectedNode)} / seq ${selectedNode.seq}`
      : `${route.nodeId} / node not found / ${totals.node_count} nodes`;
  }
  const latency = maxClusterLatency(snapshot);
  const latencyText = latency === null ? "latency n/a" : `${latency.toFixed(0)} ms max`;
  return `${totals.node_count} nodes / ${totals.online_node_count} online / ${totals.gpu_count} GPUs / ${latencyText} / seq ${snapshot.seq}`;
}
