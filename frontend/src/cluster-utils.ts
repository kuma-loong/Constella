import type { ClusterSnapshot, NodeSnapshot } from "./types";

export type FabricConfigItem = {
  count: number;
  name: string;
  architecture: string | null;
};

export function findNode(snapshot: ClusterSnapshot, nodeId: string) {
  return snapshot.nodes.find((node) => node.node_id === nodeId || node.hostname === nodeId) || null;
}

export function clusterRefreshInterval(snapshot: ClusterSnapshot | null) {
  return snapshot?.nodes.find((node) => node.status === "online")?.refresh_interval ?? snapshot?.nodes[0]?.refresh_interval ?? null;
}

export function nodeHealthPercent(totals: ClusterSnapshot["totals"]) {
  if (!totals.node_count) {
    return 0;
  }
  return (totals.online_node_count / totals.node_count) * 100;
}

export function maxClusterLatency(snapshot: ClusterSnapshot) {
  const values = snapshot.nodes
    .map((node) => (node.received_at && node.sampled_at ? (node.received_at - node.sampled_at) * 1000 : null))
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  return values.length ? Math.max(...values) : null;
}

export function fmtLatency(node: NodeSnapshot) {
  if (!node.received_at || !node.sampled_at) {
    return "latency n/a";
  }
  return `${Math.max(0, (node.received_at - node.sampled_at) * 1000).toFixed(0)} ms`;
}

export function fabricConfigSummary(snapshot: ClusterSnapshot, items: FabricConfigItem[]) {
  if (!snapshot.nodes.length) {
    return "No nodes connected";
  }
  if (!items.length) {
    return `${snapshot.nodes.length} nodes`;
  }
  const architectureCount = new Set(items.map((item) => item.architecture).filter(Boolean)).size;
  const parts = [`${snapshot.totals.gpu_count} GPUs`, `${items.length} GPU ${items.length === 1 ? "type" : "types"}`];
  if (architectureCount) {
    parts.push(`${architectureCount} ${architectureCount === 1 ? "architecture" : "architectures"}`);
  }
  return parts.join(" / ");
}

export function fabricConfigItems(snapshot: ClusterSnapshot): FabricConfigItem[] {
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

export function fabricNodeSizeClass(node: NodeSnapshot) {
  const gpuCount = node.totals.gpu_count;
  if (gpuCount >= 4) {
    return "is-node-span-4";
  }
  if (gpuCount >= 3) {
    return "is-node-span-3";
  }
  return "is-node-span-2";
}

export function compactGpuName(name: string) {
  return name.replace(/^NVIDIA\s+/, "");
}

export function sameInterval(left: number | null, right: number | null) {
  if (left === null || right === null) {
    return false;
  }
  return Math.abs(left - right) < 1e-9;
}

export function formatInterval(seconds: number) {
  return seconds < 1 ? `${seconds.toFixed(1)}s` : `${seconds.toFixed(0)}s`;
}

export function clamp(value: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(100, value));
}

export function statusClass(value: number) {
  if (value >= 80) return "is-hot";
  if (value >= 35) return "is-active";
  return "is-idle";
}

export function tempClass(value: number) {
  if (value >= 80) return "is-hot";
  if (value >= 65) return "is-warm";
  return "is-cool";
}
