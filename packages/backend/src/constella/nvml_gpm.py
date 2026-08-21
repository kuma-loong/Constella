from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass
from typing import Any

from .performance import NVIDIA_GPM_METRICS, NVIDIA_GPM_PROFILE, nvidia_gpm_enabled
from .schema import AcceleratorPerformance

NVML_SUCCESS = 0
NVML_GPM_METRIC_MAX = 333
NVML_GPM_METRICS_GET_VERSION = 1
NVML_GPM_SUPPORT_VERSION = 1

GPM_METRIC_IDS = {
    "nvidia.gpm.sm_active": 2,
    "nvidia.gpm.sm_occupancy": 3,
    "nvidia.gpm.tensor_active": 5,
    "nvidia.gpm.dram_bw_active": 10,
    "nvidia.gpm.fp64_non_tensor_active": 11,
    "nvidia.gpm.fp32_non_tensor_active": 12,
    "nvidia.gpm.fp16_non_tensor_active": 13,
}


class NvmlGpmMetricInfo(ctypes.Structure):
    _fields_ = [
        ("shortName", ctypes.c_char_p),
        ("longName", ctypes.c_char_p),
        ("unit", ctypes.c_char_p),
    ]


class NvmlGpmMetric(ctypes.Structure):
    _fields_ = [
        ("metricId", ctypes.c_uint),
        ("nvmlReturn", ctypes.c_int),
        ("value", ctypes.c_double),
        ("metricInfo", NvmlGpmMetricInfo),
    ]


class NvmlGpmMetricsGet(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint),
        ("numMetrics", ctypes.c_uint),
        ("sample1", ctypes.c_void_p),
        ("sample2", ctypes.c_void_p),
        ("metrics", NvmlGpmMetric * NVML_GPM_METRIC_MAX),
    ]


class NvmlGpmSupport(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint),
        ("isSupportedDevice", ctypes.c_uint),
    ]


@dataclass(slots=True)
class _DeviceState:
    samples: tuple[ctypes.c_void_p, ctypes.c_void_p] | None = None
    current: int = 0
    previous_at: float | None = None
    supported: bool | None = None
    errors: int = 0
    retry_at: float = 0.0


class NvidiaGpmProvider:
    def __init__(self, lib: Any, *, retry_seconds: float = 30.0):
        self._lib = lib
        self._retry_seconds = retry_seconds
        self._states: dict[int, _DeviceState] = {}
        try:
            self._available = nvidia_gpm_enabled() and self._setup_functions()
        except Exception:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def close(self) -> None:
        if not self._available:
            return
        for state in self._states.values():
            self._free_samples(state)
        self._states.clear()

    def sample(
        self,
        index: int,
        handle: ctypes.c_void_p,
        *,
        sampled_at: float,
        monotonic_at: float | None = None,
    ) -> AcceleratorPerformance:
        now = monotonic_at if monotonic_at is not None else time.monotonic()
        if not self._available:
            return self._result("unsupported", sampled_at=sampled_at)

        state = self._states.setdefault(index, _DeviceState())
        if state.retry_at > now:
            return self._result("error", sampled_at=sampled_at, error="GPM retry pending")

        try:
            if state.supported is None:
                state.supported = self._query_support(handle)
            if not state.supported:
                return self._result("unsupported", sampled_at=sampled_at)
            if state.samples is None:
                state.samples = self._allocate_samples()

            target = state.samples[state.current]
            rc = self._lib.nvmlGpmSampleGet(handle, target)
            if rc != NVML_SUCCESS:
                raise RuntimeError(f"nvmlGpmSampleGet failed with code {rc}")

            previous_at = state.previous_at
            if previous_at is None:
                state.previous_at = now
                state.current = 1 - state.current
                state.errors = 0
                return self._result("warming", sampled_at=sampled_at)

            previous = state.samples[1 - state.current]
            interval_ms = max(0.0, (now - previous_at) * 1000.0)
            if interval_ms < 100.0:
                return self._result(
                    "warming",
                    sampled_at=sampled_at,
                    interval_ms=round(interval_ms, 1),
                )
            metrics = self._metrics(previous, target)
            state.previous_at = now
            state.current = 1 - state.current
            state.errors = 0
            if not metrics:
                raise RuntimeError("NVML GPM returned no supported metrics")
            return self._result(
                "available",
                sampled_at=sampled_at,
                interval_ms=round(interval_ms, 1),
                metrics=metrics,
            )
        except Exception as exc:
            state.previous_at = None
            state.errors += 1
            if state.errors >= 3:
                state.retry_at = now + self._retry_seconds
                state.errors = 0
            return self._result("error", sampled_at=sampled_at, error=str(exc))

    def _setup_functions(self) -> bool:
        names = (
            "nvmlGpmQueryDeviceSupport",
            "nvmlGpmSampleAlloc",
            "nvmlGpmSampleFree",
            "nvmlGpmSampleGet",
            "nvmlGpmMetricsGet",
        )
        if not all(hasattr(self._lib, name) for name in names):
            return False
        self._lib.nvmlGpmQueryDeviceSupport.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(NvmlGpmSupport),
        ]
        self._lib.nvmlGpmQueryDeviceSupport.restype = ctypes.c_int
        self._lib.nvmlGpmSampleAlloc.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        self._lib.nvmlGpmSampleAlloc.restype = ctypes.c_int
        self._lib.nvmlGpmSampleFree.argtypes = [ctypes.c_void_p]
        self._lib.nvmlGpmSampleFree.restype = ctypes.c_int
        self._lib.nvmlGpmSampleGet.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._lib.nvmlGpmSampleGet.restype = ctypes.c_int
        self._lib.nvmlGpmMetricsGet.argtypes = [ctypes.POINTER(NvmlGpmMetricsGet)]
        self._lib.nvmlGpmMetricsGet.restype = ctypes.c_int
        return True

    def _query_support(self, handle: ctypes.c_void_p) -> bool:
        support = NvmlGpmSupport(version=NVML_GPM_SUPPORT_VERSION)
        rc = self._lib.nvmlGpmQueryDeviceSupport(handle, ctypes.byref(support))
        if rc != NVML_SUCCESS:
            raise RuntimeError(f"nvmlGpmQueryDeviceSupport failed with code {rc}")
        return bool(support.isSupportedDevice)

    def _allocate_samples(self) -> tuple[ctypes.c_void_p, ctypes.c_void_p]:
        allocated: list[ctypes.c_void_p] = []
        try:
            for _ in range(2):
                sample = ctypes.c_void_p()
                rc = self._lib.nvmlGpmSampleAlloc(ctypes.byref(sample))
                if rc != NVML_SUCCESS:
                    raise RuntimeError(f"nvmlGpmSampleAlloc failed with code {rc}")
                allocated.append(sample)
        except Exception:
            for sample in allocated:
                self._lib.nvmlGpmSampleFree(sample)
            raise
        return allocated[0], allocated[1]

    def _free_samples(self, state: _DeviceState) -> None:
        if state.samples is None:
            return
        for sample in state.samples:
            try:
                self._lib.nvmlGpmSampleFree(sample)
            except Exception:
                pass
        state.samples = None

    def _metrics(
        self,
        first: ctypes.c_void_p,
        second: ctypes.c_void_p,
    ) -> dict[str, float]:
        request = NvmlGpmMetricsGet(
            version=NVML_GPM_METRICS_GET_VERSION,
            numMetrics=len(NVIDIA_GPM_METRICS),
            sample1=first,
            sample2=second,
        )
        for index, metric in enumerate(NVIDIA_GPM_METRICS):
            request.metrics[index].metricId = GPM_METRIC_IDS[metric]
        rc = self._lib.nvmlGpmMetricsGet(ctypes.byref(request))
        if rc != NVML_SUCCESS:
            raise RuntimeError(f"nvmlGpmMetricsGet failed with code {rc}")
        return {
            metric: round(float(request.metrics[index].value), 3)
            for index, metric in enumerate(NVIDIA_GPM_METRICS)
            if request.metrics[index].nvmlReturn == NVML_SUCCESS
        }

    @staticmethod
    def _result(
        status: str,
        *,
        sampled_at: float,
        interval_ms: float | None = None,
        metrics: dict[str, float] | None = None,
        error: str | None = None,
    ) -> AcceleratorPerformance:
        return AcceleratorPerformance(
            profile=NVIDIA_GPM_PROFILE,
            status=status,
            sampled_at=sampled_at,
            interval_ms=interval_ms,
            metrics=metrics or {},
            error=error,
        )
