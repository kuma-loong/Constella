"""Validation shared by telemetry ingestion, aggregation and wire output."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

FLOAT32_MAX = 3.4028234663852886e38
SQLITE_INT_MAX = 2**63 - 1


@dataclass(frozen=True, slots=True)
class NumericRule:
    minimum: float = 0
    maximum: float = SQLITE_INT_MAX
    integer: bool = False
    optional: bool = False

    def accepts(self, value: Any) -> bool:
        if value is None:
            return self.optional
        return (
            type(value) in (int, float)
            and self.minimum <= value <= self.maximum
            and (not self.integer or int(value) == value)
        )


COUNT = NumericRule(integer=True)
OPTIONAL_COUNT = NumericRule(integer=True, optional=True)
PERCENT = NumericRule(maximum=100)
READING = NumericRule(minimum=-FLOAT32_MAX, maximum=FLOAT32_MAX)
TIMESTAMP = NumericRule(maximum=253402300799, optional=True)
SAMPLE_TIME = NumericRule(minimum=0.000001, maximum=253402300799)
INTERVAL = NumericRule(minimum=0.000001, maximum=86400)
NONNEGATIVE = NumericRule()
GPU_METRIC_RULES = {
    "utilization_gpu": PERCENT,
    "utilization_mem": PERCENT,
    "memory_total_mb": COUNT,
    "memory_used_mb": COUNT,
    "memory_free_mb": COUNT,
    "temperature_c": NumericRule(minimum=-273.15),
    "power_watts": NumericRule(),
    "power_limit_watts": NumericRule(),
    "clock_sm_mhz": OPTIONAL_COUNT,
    "clock_mem_mhz": OPTIONAL_COUNT,
    "max_clock_sm_mhz": OPTIONAL_COUNT,
    "max_clock_mem_mhz": OPTIONAL_COUNT,
}
BASIC_METRICS = frozenset(name for name, rule in GPU_METRIC_RULES.items() if not rule.optional)
PROCESS_METRIC_RULES = {
    "pid": NumericRule(minimum=1, integer=True),
    "gpu_memory_mb": COUNT,
    "ppid": OPTIONAL_COUNT,
    "user_uid": OPTIONAL_COUNT,
    "runtime_seconds": OPTIONAL_COUNT,
    "process_start_time": TIMESTAMP,
    "parent_start_time": TIMESTAMP,
}
OWNER_METRIC_RULES = {
    "process_count": COUNT, "total_memory_mb": COUNT, "runtime_seconds": OPTIONAL_COUNT,
}


def numeric_issues(values: dict[str, Any], rules: dict[str, NumericRule]) -> dict[str, str]:
    """Scan only declared scalars, never histories or the full snapshot tree."""
    return {name: "Invalid numeric reading; sample omitted"
            for name, rule in rules.items() if name in values and not rule.accepts(values[name])}


def filter_readings(
    metrics: dict[str, Any], rules: dict[str, NumericRule] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Any metric name is checked; the healthy path reuses the original mapping."""
    rules = rules or {}
    invalid = [name for name, value in metrics.items() if not rules.get(name, READING).accepts(value)]
    if not invalid:
        return metrics, []
    cleaned = dict(metrics)
    for name in invalid:
        del cleaned[name]
    return cleaned, invalid


def finite_number(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def finite_reading(value: Any) -> bool:
    """Telemetry caches use float32; reject values that would overflow them."""
    return type(value) in (int, float) and -FLOAT32_MAX <= value <= FLOAT32_MAX


def json_safe(value: Any) -> Any:
    """Last-resort wire protection; ingestion also records invalid metric names."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def telemetry_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except ValueError:
        # Traversal is reserved for an unexpected invalid value, not every frame.
        return json.dumps(json_safe(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
