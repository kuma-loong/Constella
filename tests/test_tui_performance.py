from __future__ import annotations

from constella_tui.performance import (
    INTERCONNECT_METRICS,
    chart_maximum,
    format_stat,
    latest_value,
    merge_rolling_points,
    metric_points,
    metric_summary,
    metrics_for_gpu,
    selected_performance_series,
)


def test_performance_payload_helpers_keep_gaps_and_select_gpu() -> None:
    payload = {
        "series": [
            {
                "gpu_uuid": "GPU-0",
                "metrics": {
                    "metric-a": {
                        "points": [
                            [1, 10],
                            [2, None],
                            ["bad", 30],
                            [3, 40],
                            [4, float("nan")],
                        ],
                        "summary": {"avg": 25, "coverage": 66.7},
                    }
                },
            }
        ]
    }

    series = selected_performance_series(payload, "GPU-0")

    assert series is not None
    assert metric_points(series, "metric-a") == (
        [1.0, 2.0, 3.0, 4.0],
        [10.0, None, 40.0, None],
    )
    assert metric_summary(series, "metric-a") == {"avg": 25, "coverage": 66.7}
    assert latest_value([10.0, None, 40.0, None]) == 40.0
    assert format_stat(40) == "40.0%"
    assert format_stat(None) == "n/a"
    assert format_stat(float("inf")) == "n/a"
    assert format_stat(512, "mib_per_second") == "512 MiB/s"
    assert format_stat(2048, "mib_per_second") == "2.00 GiB/s"
    assert chart_maximum([None, 512.0], "mib_per_second") == 1000.0


def test_performance_interconnect_metrics_follow_gpu_capabilities() -> None:
    gpu = {
        "performance": {
            "supported_metrics": [
                "nvidia.gpm.pcie_tx_per_second",
                "nvidia.gpm.pcie_rx_per_second",
            ]
        }
    }

    selected = metrics_for_gpu(gpu)

    assert {metric.key for metric in selected if metric in INTERCONNECT_METRICS} == {
        "nvidia.gpm.pcie_tx_per_second",
        "nvidia.gpm.pcie_rx_per_second",
    }
    assert all("nvlink" not in metric.key for metric in selected)


def test_performance_rolling_points_append_without_reflowing_history() -> None:
    existing = [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0), (30.0, 40.0)]

    merged = merge_rolling_points(
        existing,
        [1.0, 11.0, 21.0, 31.0, 41.0],
        [99.0, 88.0, 77.0, 44.0, 50.0],
        bin_seconds=10.0,
        columns=4,
    )

    assert merged == [
        (10.0, 20.0),
        (20.0, 30.0),
        (30.0, 44.0),
        (40.0, 50.0),
    ]


def test_performance_rolling_points_replace_tail_trim_and_reset_clock() -> None:
    assert merge_rolling_points(
        [(0.0, 10.0), (10.0, None)],
        [11.0, 21.0, 31.0],
        [20.0, 30.0, 40.0],
        bin_seconds=10.0,
        columns=3,
    ) == [(10.0, 20.0), (20.0, 30.0), (30.0, 40.0)]
    assert merge_rolling_points(
        [(100.0, 10.0)],
        [1.0, 2.0],
        [20.0, 30.0],
        bin_seconds=1.0,
        columns=3,
    ) == [(0.0, None), (1.0, 20.0), (2.0, 30.0)]


def test_performance_rolling_points_average_valid_samples_in_same_bin() -> None:
    assert merge_rolling_points(
        [],
        [1.0, 2.0, 3.0],
        [10.0, None, 70.0],
        bin_seconds=10.0,
        columns=2,
    ) == [(-10.0, None), (0.0, 40.0)]
