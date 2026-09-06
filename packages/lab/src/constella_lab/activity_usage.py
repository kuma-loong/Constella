"""Read-time ownership and interval algebra; no telemetry or identity writes."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from constella.analytics import _physical_card_key, compact_gpu_name, gpu_weight, job_key

TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class Usage:
    owner: str
    label: str
    start: float
    end: float
    card: str
    model: str
    key: str
    row: dict[str, Any]


def resolve_usage(
    rows: Iterable[Any],
    bindings: list[dict[str, Any]],
    start: float,
    end: float,
) -> list[Usage]:
    accounts: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for binding in bindings:
        accounts[(binding["node_id"], binding["unix_uid"])].append(binding)
    result = []
    for source in rows:
        row = dict(source)
        left, right = max(start, row["first_seen_at"]), min(end, row["last_seen_at"])
        if right < left:
            continue
        matches = accounts.get((row["node_id"], row.get("user_uid")), [])
        edges = sorted(
            {
                left,
                right,
                *(
                    max(left, min(right, float(b[k])))
                    for b in matches
                    for k in ("valid_from", "valid_to")
                    if b.get(k) is not None
                ),
            }
        )
        spans = list(zip(edges, edges[1:])) if right > left else [(left, right)]
        account = json.dumps(
            [
                row["node_id"],
                row.get("user_uid") if row.get("user_uid") is not None else row.get("user"),
            ]
        )
        key = job_key({**row, "first_seen_at": row["session_first_seen_at"]})
        for a, b in spans:
            applicable = [
                binding
                for binding in matches
                if binding["valid_from"] <= a
                and (binding["valid_to"] is None or a < binding["valid_to"])
            ]
            binding = max(applicable, key=lambda item: item["valid_from"], default=None)
            owner = f"user:{binding['user_id']}" if binding else f"account:{account}"
            label = (
                binding.get("user_display_name") or binding.get("user_email") or binding["user_id"]
                if binding
                else f"{row.get('user') or 'Unknown'} @ {row['node_id']}"
            )
            result.append(
                Usage(
                    owner,
                    label,
                    a,
                    b,
                    _physical_card_key(row),
                    compact_gpu_name(row.get("gpu_name")),
                    key,
                    row,
                )
            )
    return result


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


def segments(records: list[Usage], start: float, end: float) -> list[dict[str, Any]]:
    cards: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for item in records:
        cards[item.card].append((max(start, item.start), min(end, item.end)))
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


def summary(records: list[Usage], start: float, end: float) -> dict[str, Any]:
    spans = segments(records, start, end)
    return {
        "gpu_hours": sum((s["end"] - s["start"]) * s["count"] for s in spans) / 3600,
        "active_hours": sum(s["end"] - s["start"] for s in spans) / 3600,
        "active_days": len(
            {
                datetime.fromtimestamp(at, TIMEZONE).date()
                for r in records
                for at in observed_days(r, start, end)
            }
        ),
    }


def observed_days(record: Usage, start: float, end: float) -> list[float]:
    a, b = max(start, record.start), min(end, record.end)
    if b < a or a >= end or (b == a and record.start != record.end):
        return []
    day = datetime.fromtimestamp(a, TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    result = [a]
    day += timedelta(days=1)
    while day.timestamp() < b:
        result.append(day.timestamp())
        day += timedelta(days=1)
    return result


def user_statistics(
    rows: Iterable[Any], bindings: list[dict[str, Any]], start: float, end: float
) -> list[dict[str, Any]]:
    groups: dict[str, list[Usage]] = defaultdict(list)
    for item in resolve_usage(rows, bindings, start, end):
        groups[item.owner].append(item)
    result = []
    for owner, records in groups.items():
        totals = summary(records, start, end)
        models = model_statistics(records, start, end)
        result.append(
            {
                "user": records[0].label,
                "owner_key": owner,
                **totals,
                "weighted_gpu_hours": sum(m["gpu_hours"] * gpu_weight(m["model"]) for m in models),
                "task_count": len({r.row["session_id"] for r in records}),
                "job_count": len({r.key for r in records}),
                "last_seen_at": max(r.end for r in records),
                "top_gpu_models": [
                    {"name": m["model"], "gpu_hours": m["gpu_hours"]} for m in models[:3]
                ],
            }
        )
    return sorted(result, key=lambda item: (-item["weighted_gpu_hours"], item["owner_key"]))[:20]


def model_statistics(records: list[Usage], start: float, end: float) -> list[dict[str, Any]]:
    models: dict[str, list[Usage]] = defaultdict(list)
    for record in records:
        models[record.model].append(record)
    result = [
        {"model": model, "gpu_hours": summary(items, start, end)["gpu_hours"]}
        for model, items in models.items()
    ]
    total = sum(item["gpu_hours"] for item in result)
    for item in result:
        item["share"] = item["gpu_hours"] / total if total else 0
    return sorted(result, key=lambda item: (-item["gpu_hours"], item["model"]))
