from __future__ import annotations

import math
from array import array
from dataclasses import dataclass, field
from typing import Any

from .performance import NVIDIA_GPM_METRICS, NVIDIA_GPM_PROFILE


@dataclass(slots=True)
class NvidiaGpmSampleRing:
    capacity: int
    timestamps: array = field(init=False)
    values: dict[str, array] = field(init=False)
    valid_masks: array = field(init=False)
    write_index: int = 0
    count: int = 0

    def __post_init__(self) -> None:
        self.timestamps = array("d", [0.0]) * self.capacity
        self.values = {
            metric: array("f", [0.0]) * self.capacity for metric in NVIDIA_GPM_METRICS
        }
        self.valid_masks = array("B", [0]) * self.capacity

    def append(self, *, sampled_at: float, performance: dict[str, Any]) -> None:
        index = self.write_index
        self.timestamps[index] = sampled_at
        raw_metrics = performance.get("metrics")
        metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
        mask = 0
        if performance.get("status") == "available":
            for bit, metric in enumerate(NVIDIA_GPM_METRICS):
                value = metrics.get(metric)
                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                    self.values[metric][index] = float(value)
                    mask |= 1 << bit
        self.valid_masks[index] = mask
        self.write_index = (index + 1) % self.capacity
        self.count = min(self.capacity, self.count + 1)

    @property
    def oldest_at(self) -> float | None:
        return self._time_at(0) if self.count else None

    @property
    def newest_at(self) -> float | None:
        return self._time_at(self.count - 1) if self.count else None

    def metric_series(
        self,
        metric: str,
        *,
        since: float,
        until: float,
        max_points: int,
        summary_only: bool,
    ) -> dict[str, Any]:
        bit = 1 << NVIDIA_GPM_METRICS.index(metric)
        timeline: list[tuple[float, float | None]] = []
        values: list[float] = []
        for offset in range(self.count):
            index = self._index(offset)
            sampled_at = float(self.timestamps[index])
            if sampled_at < since:
                continue
            if sampled_at > until:
                break
            value = float(self.values[metric][index]) if self.valid_masks[index] & bit else None
            timeline.append((sampled_at, value))
            if value is not None:
                values.append(value)
        summary = _summary(values, expected_count=len(timeline))
        return {
            "summary": summary,
            "points": []
            if summary_only
            else _downsample_minmax(timeline, max_points=max_points),
        }

    def _time_at(self, offset: int) -> float:
        return float(self.timestamps[self._index(offset)])

    def _index(self, offset: int) -> int:
        if self.count < self.capacity:
            return offset
        return (self.write_index + offset) % self.capacity


class NvidiaGpmHighresCache:
    def __init__(self, *, capacity: int, enabled: bool = True):
        self.capacity = capacity
        self.enabled = enabled
        self.rings: dict[tuple[str, str], NvidiaGpmSampleRing] = {}
        self.devices: dict[tuple[str, str], dict[str, Any]] = {}

    def append(
        self,
        *,
        node_id: str,
        gpu_uuid: str,
        gpu_index: int,
        name: str,
        sampled_at: float,
        performance: dict[str, Any] | None,
    ) -> None:
        if not self.enabled or not performance or performance.get("profile") != NVIDIA_GPM_PROFILE:
            return
        key = (node_id, gpu_uuid)
        ring = self.rings.get(key)
        if ring is None:
            ring = NvidiaGpmSampleRing(self.capacity)
            self.rings[key] = ring
        self.devices[key] = {
            "node_id": node_id,
            "gpu_uuid": gpu_uuid,
            "gpu_index": gpu_index,
            "name": name,
            "status": str(performance.get("status") or "error"),
        }
        ring.append(sampled_at=sampled_at, performance=performance)

    def query(
        self,
        *,
        node_id: str,
        gpu_uuids: list[str] | None,
        metrics: list[str] | None,
        since: float,
        until: float,
        max_points: int,
        summary_only: bool,
    ) -> dict[str, Any]:
        selected_metrics = [
            metric for metric in (metrics or list(NVIDIA_GPM_METRICS)) if metric in NVIDIA_GPM_METRICS
        ]
        if not self.enabled:
            return {
                "enabled": False,
                "profile": NVIDIA_GPM_PROFILE,
                "since": since,
                "until": until,
                "metrics": selected_metrics,
                "series": [],
            }
        selected_uuids = set(gpu_uuids or [])
        series: list[dict[str, Any]] = []
        for key, ring in self.rings.items():
            if key[0] != node_id or (selected_uuids and key[1] not in selected_uuids):
                continue
            device = self.devices[key]
            series.append(
                {
                    **device,
                    "metrics": {
                        metric: ring.metric_series(
                            metric,
                            since=since,
                            until=until,
                            max_points=max_points,
                            summary_only=summary_only,
                        )
                        for metric in selected_metrics
                    },
                }
            )
        series.sort(key=lambda item: (item["gpu_index"], item["gpu_uuid"]))
        return {
            "enabled": True,
            "profile": NVIDIA_GPM_PROFILE,
            "since": since,
            "until": until,
            "metrics": selected_metrics,
            "series": series,
        }

    def status(self) -> dict[str, Any]:
        valid_points = sum(ring.count for ring in self.rings.values())
        bytes_per_point = 8 + len(NVIDIA_GPM_METRICS) * 4 + 1
        return {
            "enabled": self.enabled,
            "profile": NVIDIA_GPM_PROFILE,
            "ring_count": len(self.rings),
            "valid_point_count": valid_points,
            "approx_bytes": valid_points * bytes_per_point,
        }


def _summary(values: list[float], *, expected_count: int) -> dict[str, Any]:
    count = len(values)
    if not values:
        return {
            "avg": None,
            "min": None,
            "max": None,
            "p95": None,
            "sample_count": 0,
            "expected_count": expected_count,
            "coverage": 0.0,
        }
    ordered = sorted(values)
    p95_index = min(count - 1, max(0, math.ceil(count * 0.95) - 1))
    return {
        "avg": round(sum(values) / count, 3),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
        "p95": round(ordered[p95_index], 3),
        "sample_count": count,
        "expected_count": expected_count,
        "coverage": round(count / expected_count * 100.0, 2) if expected_count else 0.0,
    }


def _downsample_minmax(
    timeline: list[tuple[float, float | None]],
    *,
    max_points: int,
) -> list[list[float | None]]:
    if len(timeline) <= max_points:
        return [[sampled_at, value] for sampled_at, value in timeline]
    bucket_size = max(1, math.ceil(len(timeline) / max(1, max_points // 2)))
    points: list[tuple[float, float | None]] = []
    for start in range(0, len(timeline), bucket_size):
        bucket = timeline[start : start + bucket_size]
        valid = [item for item in bucket if item[1] is not None]
        if not valid:
            points.append((bucket[-1][0], None))
            continue
        low = min(valid, key=lambda item: float(item[1]))
        high = max(valid, key=lambda item: float(item[1]))
        points.extend(sorted({low, high}, key=lambda item: item[0]))
        if len(valid) != len(bucket):
            missing = next(item for item in bucket if item[1] is None)
            points.append(missing)
    points.sort(key=lambda item: item[0])
    return [[sampled_at, value] for sampled_at, value in points[:max_points]]
