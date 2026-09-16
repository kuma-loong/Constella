import type { ClusterSnapshot, GpuInfo } from "./types";

const labels: Record<string, string> = {
  utilization_gpu: "GPU utilization",
  utilization_mem: "Memory utilization",
  memory_used_mb: "Memory used",
  memory_total_mb: "Memory capacity",
  temperature_c: "Temperature",
  power_watts: "Power",
  "nvidia.gpm.dram_bw_active": "DRAM BW",
  "nvidia.gpm.sm_active": "SM Active",
  "nvidia.gpm.sm_occupancy": "SM Occupancy",
  "nvidia.gpm.tensor_active": "Tensor",
};

export function telemetryIssues(gpu: GpuInfo): string[] {
  const issues = Object.entries(gpu.telemetry_errors || {}).map(
    ([metric, reason]) => `${labels[metric] || metric.replaceAll("_", " ")}: ${reason}`,
  );
  if (gpu.error) issues.unshift(gpu.error);
  const performance = gpu.performance;
  if (performance?.error) issues.push(performance.error);
  for (const metric of performance?.invalid_metrics || []) {
    issues.push(`${labels[metric] || metric.replace("nvidia.gpm.", "")}: invalid reading omitted`);
  }
  return issues;
}

export function metricAvailable(gpu: GpuInfo, ...fields: string[]): boolean {
  return !gpu.error && fields.every((field) => !gpu.telemetry_errors?.[field]);
}

export function reportingCount(gpus: GpuInfo[], ...fields: string[]): number {
  return gpus.filter(gpu => metricAvailable(gpu, ...fields)).length;
}

export function TelemetryNotice({ snapshot }: { snapshot: ClusterSnapshot | null }) {
  const affected = (snapshot?.nodes || []).flatMap(node => node.gpus.flatMap(gpu => {
    const issues = telemetryIssues(gpu);
    return issues.length ? [{ node, gpu, issues }] : [];
  }));
  const affectedNodes = (snapshot?.nodes || []).flatMap(node => {
    const issues = [...(node.error ? [node.error] : []), ...Object.values(node.telemetry_errors || {})];
    return issues.length ? [{ node, issues }] : [];
  });
  if (!affected.length && !affectedNodes.length) return null;
  return (
    <aside class="telemetry-notice" aria-label="Telemetry issues">
      <details>
        <summary>
          <strong>Telemetry degraded{affected.length ? ` · ${affected.length} ${affected.length === 1 ? "GPU" : "GPUs"}` : ""}{affectedNodes.length ? ` · ${affectedNodes.length} ${affectedNodes.length === 1 ? "node" : "nodes"}` : ""}</strong>
          <span>Some readings are unavailable. Other readings continue updating.</span>
        </summary>
        <ul>{affectedNodes.map(({ node, issues }) => (
          <li key={node.node_id}>
            <a href={`/nodes/${encodeURIComponent(node.node_id)}`}>{node.node_id}</a>
            <span>{issues.join("; ")}</span>
          </li>
        ))}{affected.map(({ node, gpu, issues }) => (
          <li key={`${node.node_id}:${gpu.index}`}>
            <a href={`/nodes/${encodeURIComponent(node.node_id)}`}>{node.node_id} · GPU {gpu.index}</a>
            <span>{issues.join("; ")}</span>
          </li>
        ))}</ul>
      </details>
    </aside>
  );
}
