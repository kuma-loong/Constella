from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Any

from . import nvidia_smi
from .nvml import NVMLSampler
from .schema import GpuProcess, Snapshot

logger = logging.getLogger(__name__)

ALLOWED_REFRESH_INTERVALS = (0.5, 1.0, 2.0, 5.0)


def validate_refresh_interval(seconds: float) -> float:
    value = float(seconds)
    for allowed in ALLOWED_REFRESH_INTERVALS:
        if abs(value - allowed) < 1e-9:
            return allowed
    allowed_values = ", ".join(f"{value:g}s" for value in ALLOWED_REFRESH_INTERVALS)
    raise ValueError(f"refresh_interval must be one of: {allowed_values}")


class SnapshotCollector:
    def __init__(
        self,
        refresh_interval: float = 1.0,
        process_interval: float = 3.0,
        history_size: int = 120,
    ):
        self.refresh_interval = validate_refresh_interval(refresh_interval)
        self._process_interval = max(1.0, process_interval)
        self.history_size = history_size
        self._task: asyncio.Task[None] | None = None
        self._event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._snapshot: Snapshot | None = None
        self._seq = 0
        self._history: dict[str, dict[str, deque[float]]] = defaultdict(
            lambda: {
                "gpu": deque(maxlen=history_size),
                "memory": deque(maxlen=history_size),
                "power": deque(maxlen=history_size),
                "temperature": deque(maxlen=history_size),
            }
        )
        self._sampler: NVMLSampler | None = None
        self._next_fallback_process_at = 0.0
        self._fallback_processes_by_uuid: dict[str, list[GpuProcess]] = {}

    @property
    def process_interval(self) -> float:
        return max(self._process_interval, self.refresh_interval)

    @property
    def snapshot(self) -> Snapshot | None:
        return self._snapshot

    def settings(self) -> dict[str, Any]:
        return {
            "refresh_interval": self.refresh_interval,
            "allowed_refresh_intervals": list(ALLOWED_REFRESH_INTERVALS),
            "process_interval": self.process_interval,
        }

    def set_refresh_interval(self, seconds: float) -> dict[str, Any]:
        interval = validate_refresh_interval(seconds)
        if interval != self.refresh_interval:
            self.refresh_interval = interval
            self._wake_event.set()
        return self.settings()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="gpu-snapshot-collector")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._sampler:
            self._sampler.close()
            self._sampler = None

    async def wait_for_update(self, last_seq: int, timeout: float = 30.0) -> Snapshot | None:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if self._snapshot and self._snapshot.seq > last_seq:
                return self._snapshot
            self._event.clear()
            if self._snapshot and self._snapshot.seq > last_seq:
                return self._snapshot

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return self._snapshot
            try:
                await asyncio.wait_for(self._event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return self._snapshot

    async def _run(self) -> None:
        while True:
            started = asyncio.get_running_loop().time()
            snapshot = await asyncio.to_thread(self._sample_once)
            self._publish(snapshot)
            elapsed = asyncio.get_running_loop().time() - started
            sleep_for = max(0.0, self.refresh_interval - elapsed)
            if sleep_for:
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=sleep_for)
                except asyncio.TimeoutError:
                    pass

    def _sample_once(self) -> Snapshot:
        try:
            if self._sampler is None:
                self._sampler = NVMLSampler(process_interval=self.process_interval)
            else:
                self._sampler.set_process_interval(self.process_interval)
            return self._sampler.sample()
        except Exception as exc:
            logger.warning("NVML sample failed, falling back to nvidia-smi: %s", exc)
            if self._sampler is not None:
                self._sampler.close()
                self._sampler = None
            try:
                return self._sample_with_nvidia_smi()
            except Exception as fallback_exc:
                return nvidia_smi.error_snapshot(
                    f"NVML failed: {exc}; nvidia-smi failed: {fallback_exc}",
                    source="none",
                )

    def _sample_with_nvidia_smi(self) -> Snapshot:
        now = time.monotonic()
        collect_processes = now >= self._next_fallback_process_at
        snapshot = nvidia_smi.sample(
            timeout=2.5,
            collect_processes=collect_processes,
            cached_processes_by_uuid=self._fallback_processes_by_uuid,
        )
        if collect_processes:
            self._fallback_processes_by_uuid = {gpu.uuid: gpu.processes for gpu in snapshot.gpus}
            self._next_fallback_process_at = time.monotonic() + self.process_interval
        return snapshot

    def _publish(self, snapshot: Snapshot) -> None:
        self._seq += 1
        snapshot.seq = self._seq
        snapshot.refresh_interval = self.refresh_interval
        for gpu in snapshot.gpus:
            key = str(gpu.index)
            self._history[key]["gpu"].append(float(gpu.utilization_gpu))
            self._history[key]["memory"].append(float(gpu.memory_percent))
            self._history[key]["power"].append(float(gpu.power_percent))
            self._history[key]["temperature"].append(float(gpu.temperature_c))
        snapshot.history = self._history_payload()
        self._snapshot = snapshot
        self._event.set()

    def _history_payload(self) -> dict[str, dict[str, list[float]]]:
        payload: dict[str, dict[str, list[float]]] = {}
        for gpu_index, series in self._history.items():
            payload[gpu_index] = {name: list(values) for name, values in series.items()}
        return payload


def snapshot_to_jsonable(snapshot: Snapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {
            "ok": False,
            "source": "none",
            "error": "collector has not produced a snapshot yet",
            "seq": 0,
            "gpus": [],
            "totals": {
                "gpu_count": 0,
                "avg_gpu_utilization": 0.0,
                "avg_memory_utilization": 0.0,
                "memory_used_mb": 0,
                "memory_total_mb": 0,
                "power_watts": 0.0,
                "power_limit_watts": 0.0,
                "max_temperature_c": 0,
                "active_processes": 0,
            },
            "history": {},
        }
    return snapshot.to_dict()
