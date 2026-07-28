from __future__ import annotations

from constella_tui.charts import aligned_heatmap_rows, braille_chart, heatmap_text


def test_braille_chart_renders_labeled_dot_matrix() -> None:
    chart = braille_chart([0, 25, 50, 75, 100], width=8, height=4)

    lines = chart.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("100│")
    assert lines[-1].startswith("  0└")
    assert any("\u2801" <= character <= "\u28ff" for character in chart)


def test_braille_chart_handles_empty_data() -> None:
    assert len(braille_chart([], width=6, height=2).splitlines()) == 2


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
