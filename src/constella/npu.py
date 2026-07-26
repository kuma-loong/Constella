from __future__ import annotations

import re
import shutil
import socket
import subprocess
import time

from .schema import GpuInfo, GpuProcess, Snapshot


class NPUUnavailable(RuntimeError):
    pass


_VERSION_RE = re.compile(r"npu-smi\s+(\S+).*?Version:\s*(\S+)", re.IGNORECASE)
_DEVICE_RE = re.compile(
    r"^\|\s*(\d+)\s+(\S+)\s*\|\s*(\S+)\s*\|\s*([\d.]+|-)\s+(\d+|-).*\|$"
)
_MEMORY_RE = re.compile(r"([\d.]+)\s*/\s*([\d.]+)")


def _number(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_npu_smi(text: str) -> tuple[list[GpuInfo], str | None]:
    """Parse the human-readable ``npu-smi info`` output used by Ascend drivers."""
    lines = [line.strip() for line in text.splitlines()]
    driver = None
    for line in lines:
        match = _VERSION_RE.search(line)
        if match:
            driver = match.group(2)
            break

    devices: dict[int, GpuInfo] = {}
    physical_to_logical: dict[tuple[int, int], int] = {}
    for index, line in enumerate(lines):
        match = _DEVICE_RE.match(line)
        if not match:
            continue
        npu_id, name, health, power, temperature = match.groups()
        if name.isdigit():
            continue
        logical = int(npu_id)
        detail = lines[index + 1] if index + 1 < len(lines) else ""
        detail_parts = [part.strip() for part in detail.strip("|").split("|")]
        chip_id = 0
        physical_id = logical
        bus_id = None
        aicore = 0
        memory_used = memory_total = 0
        if len(detail_parts) >= 2:
            chip_match = re.match(r"(\d+)\s*(\d*)", detail_parts[0])
            if chip_match:
                chip_id = int(chip_match.group(1))
                if chip_match.group(2):
                    physical_id = int(chip_match.group(2))
            bus_id = detail_parts[1] if detail_parts[1] and detail_parts[1] != "NA" else None
            metrics = detail_parts[-1]
            values = re.findall(r"[\d.]+\s*/\s*[\d.]+|[\d.]+", metrics)
            if values:
                aicore = int(_number(values[0]))
            if len(values) >= 2:
                memory = _MEMORY_RE.search(values[-1])
                if memory:
                    memory_used = int(_number(memory.group(1)) * 1024 * 1024)
                    memory_total = int(_number(memory.group(2)) * 1024 * 1024)
        device = GpuInfo(
            index=len(devices),
            uuid=f"ascend-{physical_id}",
            name=name,
            pci_bus_id=bus_id,
            utilization_gpu=aicore,
            memory_total_mb=memory_total // (1024 * 1024),
            memory_used_mb=memory_used // (1024 * 1024),
            memory_free_mb=max(0, (memory_total - memory_used) // (1024 * 1024)),
            temperature_c=int(_number(temperature)),
            power_watts=_number(power),
            power_limit_watts=0.0,
            error=None if health.upper() in {"OK", "NORMAL"} else health,
        )
        devices[physical_id] = device
        physical_to_logical[(logical, chip_id)] = physical_id

    process_section = False
    for line in lines:
        if "Process id" in line and "Process name" in line:
            process_section = True
            continue
        if not process_section:
            continue
        if not line.startswith("|"):
            continue
        fields = [part.strip() for part in line.strip("|").split("|")]
        if len(fields) < 4:
            continue
        device_ids = re.findall(r"\d+", fields[0])
        if (
            len(device_ids) < 2
            or not re.fullmatch(r"\d+", fields[1])
            or not re.search(r"\d", fields[3])
        ):
            continue
        npu_id, chip_id = device_ids[:2]
        name = fields[2] or "?"
        memory = re.search(r"\d+", fields[3])
        if memory is None:
            continue
        physical_id = physical_to_logical.get((int(npu_id), int(chip_id)), int(npu_id))
        device = devices.get(physical_id)
        if device is None:
            continue
        pid = int(fields[1])
        device.processes.append(GpuProcess(pid=pid, name=name, gpu_memory_mb=int(memory.group())))

    return [devices[key] for key in sorted(devices)], driver


class NPUSampler:
    def __init__(self, command: str = "npu-smi"):
        if shutil.which(command) is None:
            raise NPUUnavailable("Cannot find npu-smi")
        self.command = command

    def sample(self) -> Snapshot:
        started = time.monotonic()
        try:
            result = subprocess.run(
                [self.command, "info"], capture_output=True, text=True, timeout=3, check=True
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise NPUUnavailable(f"npu-smi failed: {exc}") from exc
        gpus, driver = parse_npu_smi(result.stdout)
        if not gpus:
            raise NPUUnavailable("npu-smi returned no devices")
        return Snapshot(
            ok=True,
            source="npu-smi",
            hostname=socket.gethostname(),
            timestamp=time.time(),
            elapsed_ms=round((time.monotonic() - started) * 1000, 1),
            gpus=gpus,
            driver_version=driver,
        )
