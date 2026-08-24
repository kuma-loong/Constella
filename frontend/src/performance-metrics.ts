export const PERFORMANCE_METRIC_IDS = [
  "nvidia.gpm.sm_active",
  "nvidia.gpm.sm_occupancy",
  "nvidia.gpm.tensor_active",
  "nvidia.gpm.dram_bw_active",
  "nvidia.gpm.fp16_non_tensor_active",
  "nvidia.gpm.fp32_non_tensor_active",
  "nvidia.gpm.fp64_non_tensor_active",
] as const;

export type PerformanceMetricId = (typeof PERFORMANCE_METRIC_IDS)[number];

export type MetricDefinition = {
  id: PerformanceMetricId;
  label: string;
  description: string;
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
