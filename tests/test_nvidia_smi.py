from __future__ import annotations

from constella import nvidia_smi
from constella.nvidia_smi import parse_gpu_query_csv, parse_process_query_csv
from constella.schema import GpuProcess


def test_parse_gpu_query_csv() -> None:
    output = (
        "0, GPU-abc, NVIDIA RTX 6000 Ada Generation, 00000000:0F:00.0, 580.65.06, "
        "35, 73, 32, 81559, 35299, 45781, 370.91, 700.00, 1980, 2619, P0, Default, Disabled\n"
    )

    gpus, driver = parse_gpu_query_csv(output)

    assert driver == "580.65.06"
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu.index == 0
    assert gpu.uuid == "GPU-abc"
    assert gpu.memory_percent == 43.3
    assert gpu.power_percent == 53.0
    assert gpu.clock_sm_mhz == 1980


def test_parse_gpu_query_csv_handles_na() -> None:
    output = (
        "1, GPU-def, NVIDIA A100-SXM4-80GB, 00000000:34:00.0, 580.65.06, "
        "N/A, N/A, N/A, 81559, 0, 81080, N/A, 700.00, N/A, N/A, P0, Default, Disabled\n"
    )

    gpus, _ = parse_gpu_query_csv(output)

    assert gpus[0].temperature_c == 0
    assert gpus[0].utilization_gpu == 0
    assert gpus[0].clock_sm_mhz is None


def test_parse_process_query_csv() -> None:
    output = "GPU-abc, 1234, python, 4096\nGPU-abc, 2222, python, 1024\n"

    processes = parse_process_query_csv(output)

    assert set(processes) == {"GPU-abc"}
    assert [p.pid for p in processes["GPU-abc"]] == [1234, 2222]
    assert sum(p.gpu_memory_mb for p in processes["GPU-abc"]) == 5120


def test_sample_can_reuse_cached_processes(monkeypatch) -> None:
    gpu_output = (
        "0, GPU-abc, NVIDIA RTX 6000 Ada Generation, 00000000:0F:00.0, 580.65.06, "
        "35, 73, 32, 81559, 35299, 45781, 370.91, 700.00, 1980, 2619, P0, Default, Disabled\n"
    )
    calls: list[list[str]] = []

    def fake_check_output(cmd: list[str], **kwargs) -> str:
        calls.append(cmd)
        if cmd[1].startswith("--query-gpu="):
            return gpu_output
        raise AssertionError("process query should not run")

    cached = {"GPU-abc": [GpuProcess(pid=1234, name="python", gpu_memory_mb=4096)]}
    monkeypatch.setattr(nvidia_smi.subprocess, "check_output", fake_check_output)

    snapshot = nvidia_smi.sample(
        collect_processes=False,
        cached_processes_by_uuid=cached,
    )

    assert len(calls) == 1
    assert snapshot.gpus[0].processes == cached["GPU-abc"]
