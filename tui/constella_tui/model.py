from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _integer(value: object, default: int = 0) -> int:
    return int(_number(value, default))


def percent(value: object) -> str:
    return f"{_number(value):.0f}%"


def memory(value_mb: object) -> str:
    value = _number(value_mb)
    if value >= 1024:
        return f"{value / 1024:.1f} GiB"
    return f"{value:.0f} MiB"


def duration(seconds: object) -> str:
    value = max(0, _integer(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m"
    if minutes:
        return f"{minutes:d}m {secs:02d}s"
    return f"{secs:d}s"


def meter(value: object, *, width: int = 10) -> str:
    normalized = min(100.0, max(0.0, _number(value)))
    filled = round(normalized / 100 * width)
    return "█" * filled + "░" * (width - filled)


def memory_percent(gpu: dict[str, Any]) -> float:
    explicit = gpu.get("memory_percent")
    if isinstance(explicit, (int, float)):
        return float(explicit)
    total = _number(gpu.get("memory_total_mb"))
    return (_number(gpu.get("memory_used_mb")) / total * 100) if total > 0 else 0.0


def node_label(node: dict[str, Any]) -> str:
    totals = node.get("totals") if isinstance(node.get("totals"), dict) else {}
    node_id = str(node.get("node_id") or node.get("hostname") or "unknown")
    status = str(node.get("status") or "offline")
    gpu_count = _integer(totals.get("accelerator_count") or totals.get("gpu_count"))
    return f"{node_id}\n{status:<7} {gpu_count:>2} GPU"


@dataclass(frozen=True, slots=True)
class GpuRow:
    key: str
    cells: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProcessRow:
    key: str
    cells: tuple[str, ...]


def gpu_rows(node: dict[str, Any]) -> list[GpuRow]:
    rows: list[GpuRow] = []
    for raw_gpu in node.get("gpus", []):
        if not isinstance(raw_gpu, dict):
            continue
        index = _integer(raw_gpu.get("index"))
        utilization = _number(raw_gpu.get("utilization_gpu"))
        mem_percent = memory_percent(raw_gpu)
        temperature = _integer(raw_gpu.get("temperature_c"))
        power = _number(raw_gpu.get("power_watts"))
        power_limit = _number(raw_gpu.get("power_limit_watts"))
        name = str(raw_gpu.get("name") or "unknown")
        if len(name) > 24:
            name = f"{name[:21]}..."
        rows.append(
            GpuRow(
                key=str(raw_gpu.get("gpu_id") or raw_gpu.get("uuid") or index),
                cells=(
                    str(index),
                    name,
                    f"{meter(utilization)} {percent(utilization):>4}",
                    f"{memory(raw_gpu.get('memory_used_mb'))} / {memory(raw_gpu.get('memory_total_mb'))}",
                    percent(mem_percent),
                    f"{temperature} C",
                    f"{power:.0f} / {power_limit:.0f} W" if power_limit else f"{power:.0f} W",
                ),
            )
        )
    return rows


def process_rows(node: dict[str, Any]) -> list[ProcessRow]:
    rows: list[tuple[float, ProcessRow]] = []
    for raw_gpu in node.get("gpus", []):
        if not isinstance(raw_gpu, dict):
            continue
        gpu_index = _integer(raw_gpu.get("index"))
        for process in raw_gpu.get("processes", []):
            if not isinstance(process, dict):
                continue
            pid = _integer(process.get("pid"))
            task = str(process.get("task_name") or process.get("name") or "unknown")
            command = str(process.get("cmdline") or process.get("exe") or task)
            if len(command) > 52:
                command = f"{command[:49]}..."
            memory_mb = _number(process.get("gpu_memory_mb"))
            rows.append(
                (
                    memory_mb,
                    ProcessRow(
                        key=f"{gpu_index}:{pid}",
                        cells=(
                            str(gpu_index),
                            str(pid),
                            str(process.get("user") or "unknown"),
                            task,
                            memory(memory_mb),
                            duration(process.get("runtime_seconds")),
                            command,
                        ),
                    ),
                )
            )
    return [row for _, row in sorted(rows, key=lambda item: item[0], reverse=True)]
