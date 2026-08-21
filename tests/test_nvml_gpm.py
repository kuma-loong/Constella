from __future__ import annotations

import ctypes
from typing import Any, Callable

from constella.nvml_gpm import (
    NvmlGpmMetricsGet,
    NvmlGpmSupport,
    NvidiaGpmProvider,
)
from constella.performance import NVIDIA_GPM_METRICS, NVIDIA_GPM_PROFILE


class FakeFunction:
    def __init__(self, callback: Callable[..., int]):
        self.callback = callback
        self.argtypes: list[Any] = []
        self.restype: Any = None

    def __call__(self, *args: Any) -> int:
        return self.callback(*args)


class FakeGpmLibrary:
    def __init__(self, *, supported: bool = True):
        self.next_sample = 100
        self.freed: list[int] = []
        self.sample_get_calls = 0
        self.nvmlGpmQueryDeviceSupport = FakeFunction(
            lambda _handle, ptr: self._support(ptr, supported)
        )
        self.nvmlGpmSampleAlloc = FakeFunction(self._alloc)
        self.nvmlGpmSampleFree = FakeFunction(self._free)
        self.nvmlGpmSampleGet = FakeFunction(self._sample)
        self.nvmlGpmMetricsGet = FakeFunction(self._metrics)

    @staticmethod
    def _support(ptr: Any, supported: bool) -> int:
        ctypes.cast(ptr, ctypes.POINTER(NvmlGpmSupport)).contents.isSupportedDevice = supported
        return 0

    def _alloc(self, ptr: Any) -> int:
        ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p)).contents.value = self.next_sample
        self.next_sample += 1
        return 0

    def _free(self, sample: ctypes.c_void_p) -> int:
        self.freed.append(int(sample.value))
        return 0

    def _sample(self, _handle: ctypes.c_void_p, _sample: ctypes.c_void_p) -> int:
        self.sample_get_calls += 1
        return 0

    @staticmethod
    def _metrics(ptr: Any) -> int:
        request = ctypes.cast(ptr, ctypes.POINTER(NvmlGpmMetricsGet)).contents
        for index in range(request.numMetrics):
            request.metrics[index].nvmlReturn = 0
            request.metrics[index].value = 10.0 + index
        return 0


def test_gpm_provider_uses_adjacent_samples_and_frees_buffers() -> None:
    lib = FakeGpmLibrary()
    provider = NvidiaGpmProvider(lib)

    warming = provider.sample(0, ctypes.c_void_p(1), sampled_at=10.0, monotonic_at=1.0)
    available = provider.sample(0, ctypes.c_void_p(1), sampled_at=11.0, monotonic_at=2.0)
    provider.close()

    assert warming.profile == NVIDIA_GPM_PROFILE
    assert warming.status == "warming"
    assert available.status == "available"
    assert available.interval_ms == 1000.0
    assert set(available.metrics) == set(NVIDIA_GPM_METRICS)
    assert available.metrics["nvidia.gpm.sm_active"] == 10.0
    assert lib.sample_get_calls == 2
    assert lib.freed == [100, 101]


def test_gpm_provider_reports_unsupported_without_allocating() -> None:
    lib = FakeGpmLibrary(supported=False)
    provider = NvidiaGpmProvider(lib)

    result = provider.sample(0, ctypes.c_void_p(1), sampled_at=10.0)

    assert result.status == "unsupported"
    assert lib.sample_get_calls == 0
    assert lib.freed == []


def test_gpm_provider_without_symbols_is_isolated() -> None:
    provider = NvidiaGpmProvider(object())

    result = provider.sample(0, ctypes.c_void_p(1), sampled_at=10.0)

    assert provider.available is False
    assert result.status == "unsupported"
