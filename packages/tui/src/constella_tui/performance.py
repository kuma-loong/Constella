from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


PERFORMANCE_PROFILE = "nvidia.gpm.v1"
PERFORMANCE_RANGES = (("5m", 5 * 60), ("15m", 15 * 60), ("1h", 60 * 60), ("2h", 2 * 60 * 60))
PERFORMANCE_CHART_POINTS = 480


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

PERFORMANCE_PAGES = (
    ("COMPUTE + MEMORY", PERFORMANCE_METRICS[:4]),
    ("NON-TENSOR PIPELINES", PERFORMANCE_METRICS[4:]),
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


def merge_rolling_points(
    existing: list[tuple[float, float | None]],
    timestamps: list[float],
    values: list[float | None],
    *,
    bin_seconds: float,
    columns: int,
) -> list[tuple[float, float | None]]:
    """Keep fixed time bins so new samples shift a chart left by one column."""
    if bin_seconds <= 0 or columns <= 0:
        raise ValueError("bin_seconds and columns must be positive")
    incoming: dict[int, list[float | None]] = {}
    for timestamp, value in zip(timestamps, values, strict=True):
        incoming.setdefault(math.floor(timestamp / bin_seconds), []).append(value)
    if not incoming:
        return existing[-columns:]

    bucket_values: dict[int, float | None] = {}
    for bucket, bucket_samples in incoming.items():
        valid_samples = [float(value) for value in bucket_samples if value is not None]
        bucket_values[bucket] = (
            sum(valid_samples) / len(valid_samples) if valid_samples else None
        )
    newest_bucket = max(bucket_values)
    newest_existing = (
        round(existing[-1][0] / bin_seconds) if existing else None
    )
    if newest_existing is None or newest_bucket < newest_existing:
        first_bucket = newest_bucket - columns + 1
        return [
            (bucket * bin_seconds, bucket_values.get(bucket))
            for bucket in range(first_bucket, newest_bucket + 1)
        ]

    merged = list(existing)
    if newest_existing in bucket_values:
        merged[-1] = (newest_existing * bin_seconds, bucket_values[newest_existing])
    merged.extend(
        (bucket * bin_seconds, bucket_values.get(bucket))
        for bucket in range(newest_existing + 1, newest_bucket + 1)
    )
    return merged[-columns:]


def format_stat(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{number:.1f}%" if math.isfinite(number) else "n/a"


def _dict_items(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
