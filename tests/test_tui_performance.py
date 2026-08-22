from __future__ import annotations

from constella_tui.performance import (
    format_stat,
    latest_value,
    metric_points,
    metric_summary,
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
