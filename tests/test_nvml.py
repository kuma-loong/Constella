from __future__ import annotations

import ctypes
import os
from types import SimpleNamespace

import pytest

from constella.nvml import NVMLSampler, architecture_label
from constella.schema import GpuInfo, GpuProcess


@pytest.mark.parametrize("mode", ["all", "names", "aggregate"])
@pytest.mark.parametrize("fallback", [False, True])
def test_compute_graphics_overlap_counts_memory_once(monkeypatch, mode, fallback) -> None:
    monkeypatch.setattr("constella.nvml._process_detail_mode", lambda: mode)
    sampler = object.__new__(NVMLSampler)
    sampler.own_user = "owner"
    compute = GpuProcess(pid=123, name="python", user="other", gpu_memory_mb=8870)
    graphics = GpuProcess(
        pid=123, name="python", user="other", gpu_memory_mb=9330, kind="graphics"
    )
    graphics_only = GpuProcess(
        pid=456, name="renderer", user="other", gpu_memory_mb=100, kind="graphics"
    )
    sampler._running_processes = lambda _handle, _fn, kind: (
        (None if fallback else [compute]) if kind == "compute" else [graphics, graphics_only]
    )
    filled = []
    sampler._fill_process_detail = lambda p, **_kwargs: filled.append(p.pid)
    processes, others = sampler._processes(ctypes.c_void_p(1), "gpu", [{"gpu": [compute]}])
    assert filled == [123, 456]
    if mode == "aggregate":
        assert processes == []
        assert others[0].process_count == 2
        assert others[0].total_memory_mb == 9430
    else:
        assert others == []
        assert [(p.pid, p.gpu_memory_mb, p.kind) for p in processes] == [
            (123, 9330, "compute"), (456, 100, "graphics")
        ]


def test_architecture_label_maps_known_nvml_values() -> None:
    assert architecture_label(9) == "Hopper"
    assert architecture_label(10) == "Blackwell"
    assert architecture_label(0xFFFFFFFF) is None


def test_nvml_sampler_isolates_unexpected_gpm_failure() -> None:
    class FailingProvider:
        @staticmethod
        def sample(*_args, **_kwargs):
            raise RuntimeError("broken GPM provider")

    sampler = object.__new__(NVMLSampler)
    sampler._gpm = FailingProvider()

    result = sampler._sample_performance(0, ctypes.c_void_p(1), 10.0)

    assert result.status == "error"
    assert result.metrics == {}
    assert result.error == "GPM sample failed: broken GPM provider"


def test_nvml_sampler_caches_static_gpu_info_for_one_minute() -> None:
    sampler = object.__new__(NVMLSampler)
    sampler._static_gpu_info = {}
    calls: list[str] = []
    sampler._device_string = lambda _handle, name, _size: calls.append(name) or name
    sampler._uint_device_call = lambda _handle, name, _arg=None: calls.append(name) or 700000
    sampler._optional_uint_device_call = (
        lambda _handle, name, _arg=None: calls.append(name) or 0
    )
    sampler._ecc_mode = lambda _handle: calls.append("ecc") or "Enabled"
    sampler._mig_mode = lambda _handle: calls.append("mig") or "Disabled"
    handle = ctypes.c_void_p(1)

    first = sampler._static_gpu_snapshot(0, handle, monotonic_at=1.0)
    cached = sampler._static_gpu_snapshot(0, handle, monotonic_at=60.9)
    refreshed = sampler._static_gpu_snapshot(0, handle, monotonic_at=61.0)

    assert cached is first
    assert refreshed is not first
    assert calls.count("nvmlDeviceGetMaxClockInfo") == 4
    assert refreshed.power_limit_watts == 700.0


def test_nvml_sampler_uses_memory_v2_without_legacy_query() -> None:
    sampler = object.__new__(NVMLSampler)
    sampler._reserved_offsets = {}
    sampler._try_memory_v2 = lambda _handle: (80 * 1024, 20 * 1024)
    gpu = GpuInfo(index=0)

    sampler._fill_memory(gpu, ctypes.c_void_p(1))

    assert gpu.memory_total_mb == 80 * 1024
    assert gpu.memory_used_mb == 20 * 1024
    assert gpu.memory_free_mb == 60 * 1024


def test_nvml_process_query_allocates_buffer_after_size_response() -> None:
    pid = os.getpid()

    def processes(_handle, count_ptr, buffer) -> int:
        count = ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_uint))
        count.contents.value = 1
        if buffer is None:
            return 7
        buffer[0].pid = pid
        buffer[0].usedGpuMemory = 256 * 1024 * 1024
        return 0

    sampler = object.__new__(NVMLSampler)
    sampler._lib = SimpleNamespace(nvmlDeviceGetComputeRunningProcesses=processes)

    result = sampler._running_processes(
        ctypes.c_void_p(1),
        "nvmlDeviceGetComputeRunningProcesses",
        "compute",
    )

    assert result is not None
    assert len(result) == 1
    assert result[0].pid == pid
    assert result[0].gpu_memory_mb == 256


def test_nvml_process_query_reports_no_permission() -> None:
    sampler = object.__new__(NVMLSampler)
    sampler._lib = SimpleNamespace(
        nvmlDeviceGetComputeRunningProcesses=lambda *_args: 4,
    )

    result = sampler._running_processes(
        ctypes.c_void_p(1),
        "nvmlDeviceGetComputeRunningProcesses",
        "compute",
    )

    assert result is None
