"""Physical-card interval aggregation shared by rankings and personal activity."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any


def union(spans: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def card_segments(
    records: Iterable[tuple[str, float, float]], start: float, end: float
) -> list[dict[str, Any]]:
    cards: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for card, left, right in records:
        cards[card].append((max(start, left), min(end, right)))
    events: dict[float, int] = defaultdict(int)
    for spans in cards.values():
        for a, b in union(spans):
            events[a] += 1
            events[b] -= 1
    active, previous = 0, start
    result: list[dict[str, Any]] = []
    for at, delta in sorted(events.items()):
        if active and at > previous:
            if result and result[-1]["end"] == previous and result[-1]["count"] == active:
                result[-1]["end"] = at
            else:
                result.append({"start": previous, "end": at, "count": active})
        active += delta
        previous = at
    return result


def model_seconds(
    records: Iterable[tuple[str, str, float, float]], start: float, end: float
) -> Counter[str]:
    cards: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for card, model, left, right in records:
        cards[(card, model)].append((max(start, left), min(end, right)))
    totals: Counter[str] = Counter()
    for (_, model), spans in cards.items():
        totals[model] += sum(b - a for a, b in union(spans))
    return totals
