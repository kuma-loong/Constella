from __future__ import annotations

from constella_tui.charts import (
    aligned_heatmap_rows,
    braille_chart,
    heatmap_text,
    heatmap_timestamps,
    time_axis,
)


def test_braille_chart_renders_labeled_dot_matrix() -> None:
    chart = braille_chart([0, 25, 50, 75, 100], width=8, height=4)

    lines = chart.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("100│")
    assert lines[-1].startswith("  0└")
    assert any("\u2801" <= character <= "\u28ff" for character in chart)


def test_braille_chart_handles_empty_data() -> None:
    assert len(braille_chart([], width=6, height=2).splitlines()) == 2


def test_braille_chart_can_keep_live_samples_on_fixed_dot_columns() -> None:
    first = braille_chart([25], width=4, height=2, resample=False).splitlines()
    second = braille_chart([25, 75], width=4, height=2, resample=False).splitlines()

    assert all(line[4:-1].strip("\u2800") == "" for line in first)
    assert any(line[-1] != "\u2800" for line in first)
    assert all(line[4:-1].strip("\u2800") == "" for line in second)
    assert any(line[-1] != "\u2800" for line in second)


def test_braille_chart_fixed_columns_drop_old_samples_without_resampling() -> None:
    values = list(range(121))

    chart = braille_chart(values, width=60, height=3, resample=False)

    assert all(len(line) == 64 for line in chart.splitlines())


def test_braille_chart_adds_aligned_time_axis() -> None:
    chart = braille_chart(
        [10, 90],
        width=20,
        height=3,
        timestamps=[1_700_000_000, 1_700_003_600],
    )

    assert len(chart.splitlines()) == 4
    assert chart.splitlines()[-1].startswith("    ")
    assert len(chart.splitlines()[-1]) == 24


def test_heatmap_uses_semantic_color_bands() -> None:
    heatmap = heatmap_text([("GPU 0", [None, 0, 10, 40, 70, 95])])

    assert heatmap.plain == "GPU 0        ·■■■■■"
    styles = [span.style for span in heatmap.spans]
    assert "#00E5FF" in styles
    assert "#A855F7" in styles
    assert "#FF6B00" in styles


def test_heatmap_rows_align_sparse_gpu_buckets() -> None:
    rows = aligned_heatmap_rows(
        [
            {
                "gpu_index": 0,
                "buckets": [
                    {"bucket_start": 10, "avg_gpu_utilization": 20, "sample_count": 1},
                    {"bucket_start": 30, "avg_gpu_utilization": 80, "sample_count": 1},
                ],
            },
            {
                "gpu_index": 1,
                "buckets": [
                    {"bucket_start": 20, "avg_gpu_utilization": 50, "sample_count": 1}
                ],
            },
        ]
    )

    assert rows == [
        ("GPU 0", [20.0, None, 80.0]),
        ("GPU 1", [None, 50.0, None]),
    ]


def test_heatmap_renders_shared_time_axis() -> None:
    timestamps = [1_700_000_000 + offset * 3600 for offset in range(20)]
    heatmap = heatmap_text(
        [("GPU 0", [10] * 20)], max_columns=20, timestamps=timestamps
    )

    axis = heatmap.plain.splitlines()[-1]
    assert axis.startswith(" " * 13)
    assert len(axis) == 33
    assert time_axis(timestamps, width=20) in axis


def test_heatmap_timestamps_returns_union_in_order() -> None:
    items = [
        {"buckets": [{"bucket_start": 30}, {"bucket_start": 10}]},
        {"buckets": [{"bucket_start": 20}, {"bucket_start": 30}]},
    ]

    assert heatmap_timestamps(items) == [10.0, 20.0, 30.0]
