export const PERFORMANCE_METRIC_IDS = [
  "nvidia.gpm.sm_active",
  "nvidia.gpm.sm_occupancy",
  "nvidia.gpm.tensor_active",
  "nvidia.gpm.dram_bw_active",
  "nvidia.gpm.fp16_non_tensor_active",
  "nvidia.gpm.fp32_non_tensor_active",
  "nvidia.gpm.fp64_non_tensor_active",
  "nvidia.gpm.pcie_tx_per_second",
  "nvidia.gpm.pcie_rx_per_second",
  "nvidia.gpm.nvlink_tx_per_second",
  "nvidia.gpm.nvlink_rx_per_second",
] as const;

export type PerformanceMetricId = (typeof PERFORMANCE_METRIC_IDS)[number];

export const DEFAULT_PERFORMANCE_METRIC_IDS = PERFORMANCE_METRIC_IDS.slice(0, 7);

export type MetricDefinition = {
  id: PerformanceMetricId;
  label: string;
  description: string;
  unit: "percent" | "mib_per_second";
};

export type MetricGroup = {
  id: string;
  label: string;
  metrics: MetricDefinition[];
};

export const PERFORMANCE_GROUPS: MetricGroup[] = [
  {
    id: "compute",
    label: "Compute",
    metrics: [
      { id: "nvidia.gpm.sm_active", label: "SM Active", description: "Busy SM share", unit: "percent" },
      { id: "nvidia.gpm.sm_occupancy", label: "SM Occupancy", description: "Active warps vs theoretical maximum", unit: "percent" },
      { id: "nvidia.gpm.tensor_active", label: "Tensor Active", description: "All Tensor Core instruction families", unit: "percent" },
    ],
  },
  {
    id: "memory",
    label: "Memory",
    metrics: [
      { id: "nvidia.gpm.dram_bw_active", label: "DRAM Bandwidth", description: "Used bandwidth vs theoretical maximum", unit: "percent" },
    ],
  },
  {
    id: "pipelines",
    label: "Non-Tensor Pipelines",
    metrics: [
      { id: "nvidia.gpm.fp16_non_tensor_active", label: "FP16", description: "Non-Tensor FP16 activity", unit: "percent" },
      { id: "nvidia.gpm.fp32_non_tensor_active", label: "FP32", description: "Non-Tensor FP32 activity", unit: "percent" },
      { id: "nvidia.gpm.fp64_non_tensor_active", label: "FP64", description: "Non-Tensor FP64 activity", unit: "percent" },
    ],
  },
  {
    id: "interconnect",
    label: "Interconnect",
    metrics: [
      { id: "nvidia.gpm.pcie_tx_per_second", label: "PCIe TX", description: "Traffic sent from the GPU", unit: "mib_per_second" },
      { id: "nvidia.gpm.pcie_rx_per_second", label: "PCIe RX", description: "Traffic received by the GPU", unit: "mib_per_second" },
      { id: "nvidia.gpm.nvlink_tx_per_second", label: "NVLink TX", description: "Aggregate traffic sent across supported links", unit: "mib_per_second" },
      { id: "nvidia.gpm.nvlink_rx_per_second", label: "NVLink RX", description: "Aggregate traffic received across supported links", unit: "mib_per_second" },
    ],
  },
];
