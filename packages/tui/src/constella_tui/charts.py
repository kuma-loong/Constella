from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from rich.text import Text


_BRAILLE_BITS = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)


def braille_chart(
    values: Sequence[float | None],
    *,
    width: int,
    height: int,
    maximum: float = 100.0,
    timestamps: Sequence[float] | None = None,
    resample: bool = True,
) -> str:
    """Render a connected high-resolution dot-matrix curve with Unicode Braille."""
    chart_width = max(4, width)
    chart_height = max(2, height)
    pixel_width = chart_width * 2
    pixel_height = chart_height * 4
    grid = [[0 for _ in range(chart_width)] for _ in range(chart_height)]
    raw_values = [None if value is None else float(value) for value in values]
    if resample:
        samples: list[float | None] = _resample(raw_values, pixel_width)
        if not samples:
            samples = [0.0] * pixel_width
    else:
        visible = raw_values[-pixel_width:]
        samples = [None] * (pixel_width - len(visible)) + visible

    points = [
        None
        if value is None
        else (
            index,
            round((1 - min(max(value, 0.0), maximum) / maximum) * (pixel_height - 1))
            if maximum > 0
            else pixel_height - 1,
        )
        for index, value in enumerate(samples)
    ]
    previous: tuple[int, int] | None = None
    for point in points:
        if point is None:
            previous = None
            continue
        line = (point,) if previous is None else _line(previous, point)
        for x, y in line:
            cell_x, dot_x = divmod(x, 2)
            cell_y, dot_y = divmod(y, 4)
            grid[cell_y][cell_x] |= _BRAILLE_BITS[dot_y][dot_x]
        previous = point

    rows = ["".join(chr(0x2800 + bits) for bits in row) for row in grid]
    if chart_height == 2:
        labeled = [f"{maximum:>3.0f}│{rows[0]}", f"  0│{rows[1]}"]
        if timestamps:
            labeled.append(f"    {time_axis(timestamps, width=chart_width)}")
        return "\n".join(labeled)
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
    if timestamps:
        labeled.append(f"    {time_axis(timestamps, width=chart_width)}")
    return "\n".join(labeled)


def heatmap_text(
    rows: Sequence[tuple[str, Sequence[float | None]]],
    *,
    max_columns: int = 24,
    timestamps: Sequence[float] | None = None,
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
    visible_timestamps = list(timestamps or ())[-max_columns:]
    if visible_timestamps:
        if rows:
            output.append("\n")
        output.append(" " * 13)
        output.append(
            time_axis(visible_timestamps, width=len(visible_timestamps)), style="#59677A"
        )
    return output


def heatmap_timestamps(items: Sequence[dict[str, Any]]) -> list[float]:
    """Return the shared ordered time axis for analytics heatmap rows."""
    return sorted(
        {
            float(bucket["bucket_start"])
            for item in items
            for bucket in item.get("buckets", [])
            if isinstance(bucket, dict) and bucket.get("bucket_start") is not None
        }
    )


def aligned_heatmap_rows(items: Sequence[dict[str, Any]]) -> list[tuple[str, list[float | None]]]:
    """Align sparse GPU heatmap buckets on a shared time axis."""
    timestamps = heatmap_timestamps(items)
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


def time_axis(timestamps: Sequence[float], *, width: int) -> str:
    """Lay out start, optional midpoint, and end labels on a fixed-width axis."""
    values = [float(value) for value in timestamps]
    axis_width = max(1, width)
    if not values:
        return " " * axis_width
    span = max(values) - min(values)
    date_format = "%H:%M" if span < 48 * 60 * 60 else "%m-%d"
    labels = [
        datetime.fromtimestamp(values[0]).strftime(date_format),
        datetime.fromtimestamp(values[len(values) // 2]).strftime(date_format),
        datetime.fromtimestamp(values[-1]).strftime(date_format),
    ]
    canvas = [" "] * axis_width

    def place(label: str, start: int) -> None:
        for offset, character in enumerate(label):
            position = start + offset
            if 0 <= position < axis_width:
                canvas[position] = character

    place(labels[0], 0)
    place(labels[2], max(0, axis_width - len(labels[2])))
    if axis_width >= sum(len(label) for label in labels) + 4:
        place(labels[1], max(0, (axis_width - len(labels[1])) // 2))
    return "".join(canvas)


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


def _resample(values: list[float | None], count: int) -> list[float | None]:
    if not values or count <= 0:
        return []
    if len(values) == 1:
        return values * count
    if count == 1:
        return [values[-1]]
    scale = (len(values) - 1) / (count - 1)
    result: list[float | None] = []
    for index in range(count):
        position = index * scale
        left = int(position)
        right = min(left + 1, len(values) - 1)
        fraction = position - left
        left_value = values[left]
        right_value = values[right]
        if left_value is None or right_value is None:
            result.append(None)
        else:
            result.append(left_value * (1 - fraction) + right_value * fraction)
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
