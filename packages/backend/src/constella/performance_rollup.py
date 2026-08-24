from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .performance import NVIDIA_GPM_PROFILE
from .schema import AcceleratorPerformance

NVIDIA_GPM_ROLLUP_METRICS = {
    "nvidia.gpm.sm_active": "sm_active",
    "nvidia.gpm.sm_occupancy": "sm_occupancy",
    "nvidia.gpm.tensor_active": "tensor_active",
    "nvidia.gpm.dram_bw_active": "dram_bw_active",
    "nvidia.gpm.fp16_non_tensor_active": "fp16_non_tensor_active",
    "nvidia.gpm.fp32_non_tensor_active": "fp32_non_tensor_active",
    "nvidia.gpm.fp64_non_tensor_active": "fp64_non_tensor_active",
    "nvidia.gpm.pcie_tx_per_second": "pcie_tx_per_second",
    "nvidia.gpm.pcie_rx_per_second": "pcie_rx_per_second",
    "nvidia.gpm.nvlink_tx_per_second": "nvlink_tx_per_second",
    "nvidia.gpm.nvlink_rx_per_second": "nvlink_rx_per_second",
}


@dataclass(slots=True)
class NvidiaGpmRollupBucket:
    bucket_start: float
    node_id: str
    gpu_uuid: str
    expected_count: int = 0
    sums: dict[str, float] = field(default_factory=dict)
    maxima: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, performance: AcceleratorPerformance) -> None:
        if performance.profile != NVIDIA_GPM_PROFILE or performance.status == "unsupported":
            return
        self.expected_count += 1
        if performance.status != "available":
            return
        for metric, value in performance.metrics.items():
            stem = NVIDIA_GPM_ROLLUP_METRICS.get(metric)
            if stem is None:
                continue
            numeric = float(value)
            self.sums[stem] = self.sums.get(stem, 0.0) + numeric
            self.maxima[stem] = max(self.maxima.get(stem, numeric), numeric)
            self.counts[stem] = self.counts.get(stem, 0) + 1

    def to_row(self, bucket_seconds: int) -> dict[str, Any]:
        row: dict[str, Any] = {
            "bucket_start": self.bucket_start,
            "bucket_seconds": bucket_seconds,
            "node_id": self.node_id,
            "gpu_uuid": self.gpu_uuid,
            "expected_count": self.expected_count,
        }
        for stem in NVIDIA_GPM_ROLLUP_METRICS.values():
            count = self.counts.get(stem, 0)
            row[f"avg_{stem}"] = self.sums.get(stem, 0.0) / count if count else None
            row[f"max_{stem}"] = self.maxima.get(stem) if count else None
            row[f"{stem}_count"] = count
        return row


def nvidia_gpm_table_sql() -> str:
    metric_columns = ",\n".join(
        f"avg_{stem} REAL, max_{stem} REAL, {stem}_count INTEGER NOT NULL DEFAULT 0"
        for stem in NVIDIA_GPM_ROLLUP_METRICS.values()
    )
    return f"""
    CREATE TABLE IF NOT EXISTS nvidia_gpm_rollups (
      bucket_start REAL NOT NULL,
      bucket_seconds INTEGER NOT NULL,
      node_id TEXT NOT NULL,
      gpu_uuid TEXT NOT NULL,
      expected_count INTEGER NOT NULL,
      {metric_columns},
      PRIMARY KEY(bucket_start, bucket_seconds, node_id, gpu_uuid)
    );

    CREATE INDEX IF NOT EXISTS idx_nvidia_gpm_rollups_node_bucket_time
      ON nvidia_gpm_rollups(node_id, bucket_seconds, bucket_start);
    """


def nvidia_gpm_row_columns() -> list[str]:
    columns = ["bucket_start", "bucket_seconds", "node_id", "gpu_uuid", "expected_count"]
    for stem in NVIDIA_GPM_ROLLUP_METRICS.values():
        columns.extend((f"avg_{stem}", f"max_{stem}", f"{stem}_count"))
    return columns
