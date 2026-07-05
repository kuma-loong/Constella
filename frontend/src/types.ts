export type GpuProcess = {
  pid: number;
  name: string;
  ppid?: number | null;
  task_name?: string | null;
  exe?: string | null;
  cmdline?: string | null;
  cmdline_hash?: string | null;
  gpu_memory_mb: number;
  user?: string | null;
  kind: string;
  runtime_seconds?: number | null;
  process_start_time?: number | null;
  parent_start_time?: number | null;
  detail_status?: string | null;
  detail_error?: string | null;
};

export type OtherUserMemory = {
  user: string;
  process_count: number;
  total_memory_mb: number;
  runtime_seconds?: number | null;
};

export type GpuHardwareInfo = {
  index: number;
  uuid: string;
  name: string;
  architecture?: string | null;
};

export type NodeHardware = {
  gpus: GpuHardwareInfo[];
};

export type GpuInfo = {
  index: number;
  node_id?: string | null;
  gpu_id?: string | null;
  uuid: string;
  name: string;
  pci_bus_id?: string | null;
  utilization_gpu: number;
  utilization_mem: number;
  memory_total_mb: number;
  memory_used_mb: number;
  memory_free_mb: number;
  memory_percent: number;
  temperature_c: number;
  power_watts: number;
  power_limit_watts: number;
  power_percent: number;
  clock_sm_mhz?: number | null;
  clock_mem_mhz?: number | null;
  max_clock_sm_mhz?: number | null;
  max_clock_mem_mhz?: number | null;
  pstate?: string | null;
  compute_mode?: string | null;
  mig_mode?: string | null;
  ecc_mode?: string | null;
  processes: GpuProcess[];
  other_users: OtherUserMemory[];
  error?: string | null;
};

export type NodeTotals = {
  gpu_count: number;
  avg_gpu_utilization: number;
  avg_memory_utilization: number;
  memory_used_mb: number;
  memory_total_mb: number;
  power_watts: number;
  power_limit_watts: number;
  max_temperature_c: number;
  active_processes: number;
};

export type NodeSnapshot = {
  node_id: string;
  hostname: string;
  seq: number;
  sampled_at: number;
  received_at?: number | null;
  refresh_interval: number;
  process_interval: number;
  status: "online" | "stale" | "offline" | "error" | string;
  source: string;
  gpus: GpuInfo[];
  totals: NodeTotals;
  error?: string | null;
  agent_version?: string | null;
  driver_version?: string | null;
  cuda_driver_version?: string | null;
  nvml_version?: string | null;
  elapsed_ms?: number;
  history: Record<string, Record<string, number[]>>;
  hardware?: NodeHardware | null;
};

export type ClusterSnapshot = {
  ok: boolean;
  seq: number;
  timestamp: number;
  nodes: NodeSnapshot[];
  totals: NodeTotals & {
    node_count: number;
    online_node_count: number;
    stale_node_count: number;
    offline_node_count: number;
  };
  history: Record<string, Record<string, number[]>>;
};

export type Settings = {
  refresh_interval: number;
  allowed_refresh_intervals: number[];
  process_interval: number;
};

export type ThemeMode = "system" | "light" | "dark";
export type LiveState = "connecting" | "live" | "paused" | "offline" | "error";
