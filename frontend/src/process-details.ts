import { deviceLabel } from "./cluster-utils";
import type { GpuProcess, NodeSnapshot } from "./types";

export type ProcessDeviceUsage = {
  label: string;
  gpuUuid: string;
  memoryMb: number;
};

export type ProcessDetail = {
  key: string;
  nodeId: string;
  hostname: string;
  pid: number;
  ppid: number | null;
  user: string;
  task: string;
  name: string;
  kind: string;
  exe: string | null;
  cmdline: string | null;
  runtime: number | null;
  processStartTime: number | null;
  parentStartTime: number | null;
  detailStatus: string | null;
  detailError: string | null;
  devices: ProcessDeviceUsage[];
  totalMemoryMb: number;
};

export type ProcessRow = {
  key: string;
  detail: ProcessDetail | null;
  node: string;
  devices: string[];
  user: string;
  pid: string;
  task: string;
  memory: number;
  runtime: number | null;
  kind: string;
  title: string;
};

export function buildProcessView(node: NodeSnapshot | null) {
  if (!node) {
    return { details: [] as ProcessDetail[], rows: [] as ProcessRow[] };
  }

  const byKey = new Map<string, ProcessDetail>();
  const aggregateRows: ProcessRow[] = [];

  for (const gpu of node.gpus) {
    for (const process of gpu.processes || []) {
      const key = processKey(node.node_id, process);
      const detail = byKey.get(key) || createProcessDetail(node, process, key);
      mergeProcessFields(detail, process);
      detail.devices.push({
        label: deviceLabel(node, gpu),
        gpuUuid: gpu.uuid,
        memoryMb: process.gpu_memory_mb,
      });
      detail.totalMemoryMb += process.gpu_memory_mb;
      byKey.set(key, detail);
    }

    for (const other of gpu.other_users || []) {
      aggregateRows.push({
        key: `aggregate:${gpu.uuid}:${other.user}`,
        detail: null,
        node: node.node_id,
        devices: [deviceLabel(node, gpu)],
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

  const details = Array.from(byKey.values()).sort(
    (left, right) => right.totalMemoryMb - left.totalMemoryMb || (right.runtime || 0) - (left.runtime || 0),
  );
  const rows = [
    ...details.map(processRow),
    ...aggregateRows,
  ];
  return { details, rows };
}

export function jobsHref(detail: ProcessDetail) {
  const params = new URLSearchParams({
    pid: String(detail.pid),
    node_id: detail.nodeId,
  });
  if (detail.processStartTime != null) {
    params.set("since", String(Math.max(0, detail.processStartTime - 60)));
  }
  return `/jobs?${params.toString()}`;
}

function processKey(nodeId: string, process: GpuProcess) {
  return `${nodeId}:${process.pid}:${process.process_start_time ?? "unknown"}`;
}

function createProcessDetail(node: NodeSnapshot, process: GpuProcess, key: string): ProcessDetail {
  return {
    key,
    nodeId: node.node_id,
    hostname: node.hostname,
    pid: process.pid,
    ppid: process.ppid ?? null,
    user: process.user || "unknown",
    task: process.task_name || process.name,
    name: process.name,
    kind: process.kind,
    exe: process.exe ?? null,
    cmdline: process.cmdline ?? null,
    runtime: process.runtime_seconds ?? null,
    processStartTime: process.process_start_time ?? null,
    parentStartTime: process.parent_start_time ?? null,
    detailStatus: process.detail_status ?? null,
    detailError: process.detail_error ?? null,
    devices: [],
    totalMemoryMb: 0,
  };
}

function mergeProcessFields(detail: ProcessDetail, process: GpuProcess) {
  detail.ppid ??= process.ppid ?? null;
  detail.exe ??= process.exe ?? null;
  detail.cmdline ??= process.cmdline ?? null;
  if (process.runtime_seconds != null) {
    detail.runtime = Math.max(detail.runtime ?? 0, process.runtime_seconds);
  }
  detail.processStartTime ??= process.process_start_time ?? null;
  detail.parentStartTime ??= process.parent_start_time ?? null;
  detail.detailStatus ??= process.detail_status ?? null;
  detail.detailError ??= process.detail_error ?? null;
}

function processRow(detail: ProcessDetail): ProcessRow {
  return {
    key: detail.key,
    detail,
    node: detail.nodeId,
    devices: detail.devices.map((device) => device.label),
    user: detail.user,
    pid: String(detail.pid),
    task: detail.task,
    memory: detail.totalMemoryMb,
    runtime: detail.runtime,
    kind: detail.kind,
    title: detail.cmdline || detail.exe || detail.name,
  };
}
