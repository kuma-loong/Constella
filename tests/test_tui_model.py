from __future__ import annotations

from constella_tui.model import (
    duration,
    gpu_rows,
    memory,
    memory_pair,
    meter,
    node_label,
    process_rows,
)


def test_tui_formatters_handle_boundaries() -> None:
    assert memory(512) == "512 MiB"
    assert memory(1536) == "1.5 GiB"
    assert memory_pair(0, 81510) == "0/79.6 GiB"
    assert memory_pair(17203, 81510) == "16.8/79.6 GiB"
    assert duration(3661) == "1h 01m"
    assert meter(-5) == "▁" * 10
    assert meter(150) == "▆" * 10


def test_tui_rows_present_gpu_and_process_data() -> None:
    node = {
        "node_id": "gpu-a",
        "status": "online",
        "totals": {"accelerator_count": 1},
        "gpus": [
            {
                "index": 0,
                "uuid": "GPU-test",
                "name": "NVIDIA A100-SXM4-40GB",
                "utilization_gpu": 72,
                "memory_used_mb": 10240,
                "memory_total_mb": 40960,
                "temperature_c": 61,
                "power_watts": 210,
                "power_limit_watts": 400,
                "processes": [
                    {
                        "pid": 42,
                        "user": "alice",
                        "task_name": "train.py",
                        "gpu_memory_mb": 9000,
                        "runtime_seconds": 93,
                        "cmdline": "python train.py",
                    },
                    {
                        "pid": 43,
                        "user": "bob",
                        "name": "worker",
                        "gpu_memory_mb": 512,
                        "runtime_seconds": 4,
                    },
                ],
            }
        ],
    }

    assert node_label(node) == "gpu-a\nonline   1 GPU"
    assert gpu_rows(node)[0].cells == (
        "0",
        "NVIDIA A100-SXM4-40GB",
        "▆▆▆▆▆▆▆▁▁▁   72%",
        "▆▆▁▁▁▁▁▁▁▁  10.0/40.0 GiB",
        "61 C",
        "210 / 400 W",
    )
    rows = process_rows(node)
    assert [row.cells[1] for row in rows] == ["42", "43"]
    assert rows[0].cells[4:6] == ("8.8 GiB", "1m 33s")
