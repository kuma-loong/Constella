from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


PERFORMANCE_PROFILE = "nvidia.gpm.v1"
PERFORMANCE_RANGES = (("5m", 5 * 60), ("15m", 15 * 60), ("1h", 60 * 60), ("2h", 2 * 60 * 60))


@dataclass(frozen=True, slots=True)
class PerformanceMetric:
    key: str
    slug: str
    label: str
    group: str
    style: str


PERFORMANCE_METRICS = (
    PerformanceMetric("nvidia.gpm.sm_active", "sm-active", "SM ACTIVE", "COMPUTE", "#00E5FF"),
    PerformanceMetric(
        "nvidia.gpm.sm_occupancy", "sm-occupancy", "SM OCCUPANCY", "COMPUTE", "#38BDF8"
    ),
    PerformanceMetric(
        "nvidia.gpm.tensor_active", "tensor-active", "TENSOR ACTIVE", "COMPUTE", "#A855F7"
    ),
    PerformanceMetric(
        "nvidia.gpm.dram_bw_active", "dram-bandwidth", "DRAM BANDWIDTH", "MEMORY", "#FF6B00"
    ),
    PerformanceMetric(
        "nvidia.gpm.fp16_non_tensor_active", "fp16", "FP16 NON-TENSOR", "PIPELINE", "#22D3EE"
    ),
    PerformanceMetric(
        "nvidia.gpm.fp32_non_tensor_active", "fp32", "FP32 NON-TENSOR", "PIPELINE", "#60A5FA"
    ),
    PerformanceMetric(
        "nvidia.gpm.fp64_non_tensor_active", "fp64", "FP64 NON-TENSOR", "PIPELINE", "#F472B6"
    ),
)


def selected_performance_series(
    payload: dict[str, Any], gpu_uuid: str
) -> dict[str, Any] | None:
    for item in _dict_items(payload.get("series")):
        if str(item.get("gpu_uuid") or "") == gpu_uuid:
            return item
    return None


def metric_points(
    series: dict[str, Any], metric: str
) -> tuple[list[float], list[float | None]]:
    metrics = series.get("metrics") if isinstance(series.get("metrics"), dict) else {}
    item = metrics.get(metric) if isinstance(metrics.get(metric), dict) else {}
    timestamps: list[float] = []
    values: list[float | None] = []
    points = item.get("points") if isinstance(item.get("points"), list) else []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            continue
        try:
            sampled_at = float(point[0])
            value = None if point[1] is None else float(point[1])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(sampled_at):
            continue
        if value is not None and not math.isfinite(value):
            value = None
        timestamps.append(sampled_at)
        values.append(value)
    return timestamps, values


def metric_summary(series: dict[str, Any], metric: str) -> dict[str, Any]:
    metrics = series.get("metrics") if isinstance(series.get("metrics"), dict) else {}
    item = metrics.get(metric) if isinstance(metrics.get(metric), dict) else {}
    return item.get("summary") if isinstance(item.get("summary"), dict) else {}


def latest_value(values: list[float | None]) -> float | None:
    return next((value for value in reversed(values) if value is not None), None)


def format_stat(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{number:.1f}%" if math.isfinite(number) else "n/a"


def _dict_items(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
