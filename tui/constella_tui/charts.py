from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rich.text import Text


_BRAILLE_BITS = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)


def braille_chart(
    values: Sequence[float],
    *,
    width: int,
    height: int,
    maximum: float = 100.0,
) -> str:
    """Render a connected high-resolution dot-matrix curve with Unicode Braille."""
    chart_width = max(4, width)
    chart_height = max(2, height)
    pixel_width = chart_width * 2
    pixel_height = chart_height * 4
    grid = [[0 for _ in range(chart_width)] for _ in range(chart_height)]
    samples = _resample([float(value) for value in values], pixel_width)
    if not samples:
        samples = [0.0] * pixel_width

    points = [
        (
            index,
            round(
                (1 - min(max(value, 0.0), maximum) / maximum)
                * (pixel_height - 1)
            )
            if maximum > 0
            else pixel_height - 1,
        )
        for index, value in enumerate(samples)
    ]
    for start, end in zip(points, points[1:]):
        for x, y in _line(start, end):
            cell_x, dot_x = divmod(x, 2)
            cell_y, dot_y = divmod(y, 4)
            grid[cell_y][cell_x] |= _BRAILLE_BITS[dot_y][dot_x]

    rows = ["".join(chr(0x2800 + bits) for bits in row) for row in grid]
    if chart_height == 2:
        return f"{maximum:>3.0f}│{rows[0]}\n  0│{rows[1]}"
    middle = chart_height // 2
    labeled = []
    for index, row in enumerate(rows):
        if index == 0:
            label = f"{maximum:>3.0f}│"
        elif index == middle:
            label = f"{maximum / 2:>3.0f}│"
        elif index == chart_height - 1:
            label = "  0└"
        else:
            label = "   │"
        labeled.append(f"{label}{row}")
    return "\n".join(labeled)


def heatmap_text(
    rows: Sequence[tuple[str, Sequence[float | None]]],
    *,
    max_columns: int = 24,
) -> Text:
    """Render GPU utilization buckets as a compact semantic-color matrix."""
    output = Text()
    for row_index, (label, values) in enumerate(rows):
        if row_index:
            output.append("\n")
        output.append(f"{label:<12.12} ", style="#8A99AD")
        for value in list(values)[-max_columns:]:
            if value is None:
                output.append("·", style="#2B334A")
            else:
                output.append("■", style=_heat_style(float(value)))
    return output


def aligned_heatmap_rows(items: Sequence[dict[str, Any]]) -> list[tuple[str, list[float | None]]]:
    """Align sparse GPU heatmap buckets on a shared time axis."""
    timestamps = sorted(
        {
            float(bucket["bucket_start"])
            for item in items
            for bucket in item.get("buckets", [])
            if isinstance(bucket, dict) and bucket.get("bucket_start") is not None
        }
    )
    rows: list[tuple[str, list[float | None]]] = []
    for item in items:
        values_by_time: dict[float, float] = {}
        for bucket in item.get("buckets", []):
            if not isinstance(bucket, dict) or bucket.get("bucket_start") is None:
                continue
            if bucket.get("sample_count") and bucket.get("avg_gpu_utilization") is not None:
                values_by_time[float(bucket["bucket_start"])] = float(
                    bucket["avg_gpu_utilization"]
                )
        label = f"GPU {item.get('gpu_index') if item.get('gpu_index') is not None else '?'}"
        rows.append((label, [values_by_time.get(timestamp) for timestamp in timestamps]))
    return rows


def _heat_style(value: float) -> str:
    if value >= 85:
        return "#FF6B00"
    if value >= 60:
        return "#A855F7"
    if value >= 20:
        return "#00E5FF"
    if value > 0:
        return "#38556B"
    return "#2B334A"


def _resample(values: list[float], count: int) -> list[float]:
    if not values or count <= 0:
        return []
    if len(values) == 1:
        return values * count
    if count == 1:
        return [values[-1]]
    scale = (len(values) - 1) / (count - 1)
    result: list[float] = []
    for index in range(count):
        position = index * scale
        left = int(position)
        right = min(left + 1, len(values) - 1)
        fraction = position - left
        result.append(values[left] * (1 - fraction) + values[right] * fraction)
    return result


def _line(start: tuple[int, int], end: tuple[int, int]):
    x0, y0 = start
    x1, y1 = end
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        yield x0, y0
        if x0 == x1 and y0 == y1:
            return
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x0 += sx
        if doubled <= dx:
            error += dx
            y0 += sy
