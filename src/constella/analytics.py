from __future__ import annotations

import math
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .db import ROLLUP_1H, ROLLUP_2M, ROLLUP_20S, SQLiteStore

TIMEZONE = "Asia/Shanghai"
DEFAULT_GPU_WEIGHTS = {
    "H100": 1.0,
    "PRO 6000": 0.9,
    "DEFAULT": 1.0,
}
RANGES = {
    "1h": 60 * 60,
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
}


@dataclass(slots=True)
class UserUsage:
    user: str
    gpu_hours: float = 0.0
    weighted_gpu_hours: float = 0.0
    sessions: set[str] = field(default_factory=set)
    jobs: set[str] = field(default_factory=set)
    last_seen_at: float = 0.0
    gpu_model_seconds: Counter[str] = field(default_factory=Counter)


@dataclass(slots=True)
class JobUsage:
    job_key: str
    user: str
    node_id: str
    task_name: str
    started_at: float
    last_seen_at: float
    status: str
    gpu_hours: float = 0.0
    weighted_gpu_hours: float = 0.0
    sessions: set[str] = field(default_factory=set)
    gpu_uuids: set[str] = field(default_factory=set)
    memory_seconds: float = 0.0
    usage_seconds: float = 0.0

    @property
    def avg_memory_mb(self) -> float:
        if self.usage_seconds <= 0:
            return 0.0
        return self.memory_seconds / self.usage_seconds


def overview_analytics(store: SQLiteStore, *, range_name: str = "7d", now: float | None = None) -> dict[str, Any]:
    range_end = time.time() if now is None else now
    range_start = range_end - _range_seconds(range_name, default="7d")
    rows = _usage_rows(store, range_start=range_start, range_end=range_end)
    users, jobs = _roll_up_usage(rows, range_start=range_start, range_end=range_end)
    jobs_sorted = sorted(jobs.values(), key=lambda item: item.weighted_gpu_hours, reverse=True)

    return {
        **_meta(range_start=range_start, range_end=range_end, generated_at=range_end),
        "user_gpu_hours": [_user_payload(item) for item in _top(users.values(), "weighted_gpu_hours", 20)],
        "job_rankings": [_job_payload(item) for item in jobs_sorted[:20]],
        "anomalies": _anomaly_payloads(store, jobs_sorted, range_start=range_start, range_end=range_end),
        "off_hours": _off_hours(rows, range_start=range_start, range_end=range_end),
    }


def node_analytics(
    store: SQLiteStore,
    *,
    node_id: str,
    range_name: str = "24h",
    now: float | None = None,
) -> dict[str, Any]:
    range_end = time.time() if now is None else now
    range_start = range_end - _range_seconds(range_name, default="24h")
    source_bucket = _select_rollup_bucket(range_start=range_start, range_end=range_end)
    series_bucket = _target_bucket(range_end - range_start, source_bucket, target_points=560)
    heatmap_bucket = _heatmap_bucket(range_end - range_start)
    gpus = _node_gpus(store, node_id)
    return {
        **_meta(
            range_start=range_start,
            range_end=range_end,
            generated_at=range_end,
            bucket_seconds=series_bucket,
        ),
        "node_id": node_id,
        "gpus": gpus,
        "series": _rollup_series(
            store,
            node_id=node_id,
            range_start=range_start,
            range_end=range_end,
            source_bucket=source_bucket,
            target_bucket=series_bucket,
        ),
        "heatmap": _rollup_heatmap(
            store,
            node_id=node_id,
            range_start=range_start,
            range_end=range_end,
            source_bucket=source_bucket,
            target_bucket=heatmap_bucket,
        ),
        "heatmap_bucket_seconds": heatmap_bucket,
    }


def gpu_weight(name: str | None) -> float:
    normalized = " ".join((name or "").upper().split())
    for key, weight in DEFAULT_GPU_WEIGHTS.items():
        if key != "DEFAULT" and key in normalized:
            return weight
    return DEFAULT_GPU_WEIGHTS["DEFAULT"]


def job_key(row: sqlite3.Row | dict[str, Any]) -> str:
    start = _value(row, "parent_start_time") or _value(row, "process_start_time") or _value(row, "first_seen_at")
    parent = _value(row, "ppid") or _value(row, "pid")
    return f"{_value(row, 'node_id')}:{_value(row, 'user') or 'unknown'}:{start}:{parent}"


def overlap_seconds(first_seen_at: float, last_seen_at: float, range_start: float, range_end: float) -> float:
    return max(0.0, min(last_seen_at, range_end) - max(first_seen_at, range_start))


def _usage_rows(store: SQLiteStore, *, range_start: float, range_end: float) -> list[sqlite3.Row]:
    return store.connection.execute(
        """
        SELECT
          s.session_id, s.node_id, s.pid, s.ppid, s.process_start_time, s.parent_start_time,
          s.user, s.task_name, s.process_name, s.first_seen_at AS session_first_seen_at,
          s.last_seen_at AS session_last_seen_at, s.duration_seconds, s.status,
          u.gpu_uuid, u.first_seen_at, u.last_seen_at, u.avg_memory_mb, u.max_memory_mb,
          g.name AS gpu_name, g.gpu_index
        FROM process_gpu_usages u
        JOIN process_sessions s ON s.session_id = u.session_id
        LEFT JOIN gpus g ON g.node_id = u.node_id AND g.uuid = u.gpu_uuid
        WHERE u.last_seen_at >= ? AND u.first_seen_at <= ?
        """,
        (range_start, range_end),
    ).fetchall()


def _roll_up_usage(
    rows: list[sqlite3.Row],
    *,
    range_start: float,
    range_end: float,
) -> tuple[dict[str, UserUsage], dict[str, JobUsage]]:
    users: dict[str, UserUsage] = {}
    jobs: dict[str, JobUsage] = {}
    for row in rows:
        seconds = overlap_seconds(row["first_seen_at"], row["last_seen_at"], range_start, range_end)
        if seconds <= 0:
            continue
        weight = gpu_weight(row["gpu_name"])
        user = row["user"] or "unknown"
        key = job_key(row)
        user_usage = users.setdefault(user, UserUsage(user=user))
        user_usage.gpu_hours += seconds / 3600
        user_usage.weighted_gpu_hours += seconds * weight / 3600
        user_usage.sessions.add(row["session_id"])
        user_usage.jobs.add(key)
        user_usage.last_seen_at = max(user_usage.last_seen_at, row["last_seen_at"])
        user_usage.gpu_model_seconds[compact_gpu_name(row["gpu_name"])] += seconds

        job = jobs.get(key)
        if job is None:
            job = JobUsage(
                job_key=key,
                user=user,
                node_id=row["node_id"],
                task_name=row["task_name"],
                started_at=row["session_first_seen_at"],
                last_seen_at=row["session_last_seen_at"],
                status=row["status"],
            )
            jobs[key] = job
        job.started_at = min(job.started_at, row["session_first_seen_at"])
        job.last_seen_at = max(job.last_seen_at, row["session_last_seen_at"])
        if row["status"] == "running":
            job.status = "running"
        job.gpu_hours += seconds / 3600
        job.weighted_gpu_hours += seconds * weight / 3600
        job.sessions.add(row["session_id"])
        job.gpu_uuids.add(row["gpu_uuid"])
        job.memory_seconds += float(row["avg_memory_mb"] or 0.0) * seconds
        job.usage_seconds += seconds
    return users, jobs


def _anomaly_payloads(
    store: SQLiteStore,
    jobs: list[JobUsage],
    *,
    range_start: float,
    range_end: float,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    recent_start = max(range_start, range_end - 3600)
    for job in jobs:
        duration = max(0.0, job.last_seen_at - job.started_at)
        if duration < 7200 or job.avg_memory_mb < 20 * 1024:
            continue
        recent_avg = _avg_gpu_utilization(
            store,
            node_id=job.node_id,
            gpu_uuids=job.gpu_uuids,
            since=recent_start,
            until=range_end,
        )
        lifetime_avg = _avg_gpu_utilization(
            store,
            node_id=job.node_id,
            gpu_uuids=job.gpu_uuids,
            since=max(range_start, job.started_at),
            until=min(range_end, job.last_seen_at),
        )
        decision_avg = recent_avg if recent_avg is not None else lifetime_avg
        if decision_avg is None or decision_avg >= 5.0:
            continue
        items.append(
            {
                "user": job.user,
                "node_id": job.node_id,
                "task_name": job.task_name,
                "duration_seconds": duration,
                "gpu_memory_gb": round(job.avg_memory_mb / 1024, 1),
                "recent_avg_gpu_utilization": round(recent_avg if recent_avg is not None else decision_avg, 1),
                "lifetime_avg_gpu_utilization": round(lifetime_avg or 0.0, 1),
                "idle_tail_seconds": 3600 if recent_avg is not None else 0,
                "gpu_uuids": sorted(job.gpu_uuids),
                "last_seen_at": job.last_seen_at,
                "reason": "long memory-heavy job with low recent GPU utilization",
            }
        )
        if len(items) >= 20:
            break
    return items


def _avg_gpu_utilization(
    store: SQLiteStore,
    *,
    node_id: str,
    gpu_uuids: set[str],
    since: float,
    until: float,
) -> float | None:
    if not gpu_uuids or until <= since:
        return None
    placeholders = ",".join("?" for _ in gpu_uuids)
    bucket = _select_rollup_bucket(range_start=since, range_end=until)
    row = store.connection.execute(
        f"""
        SELECT SUM(avg_gpu_utilization * sample_count) / SUM(sample_count) AS value
        FROM gpu_metric_rollups
        WHERE bucket_seconds = ?
          AND node_id = ?
          AND gpu_uuid IN ({placeholders})
          AND bucket_start >= ?
          AND bucket_start <= ?
        """,
        [bucket, node_id, *sorted(gpu_uuids), since, until],
    ).fetchone()
    if row is None or row["value"] is None:
        return None
    return float(row["value"])


def _off_hours(rows: list[sqlite3.Row], *, range_start: float, range_end: float) -> dict[str, Any]:
    tz = ZoneInfo(TIMEZONE)
    night_sessions: set[str] = set()
    weekend_sessions: set[str] = set()
    user_counts: Counter[str] = Counter()
    night_gpu_seconds = 0.0
    weekend_gpu_seconds = 0.0
    for row in rows:
        started_at = max(row["session_first_seen_at"], range_start)
        started = datetime.fromtimestamp(started_at, tz=tz)
        seconds = overlap_seconds(row["first_seen_at"], row["last_seen_at"], range_start, range_end)
        user = row["user"] or "unknown"
        if 0 <= started.hour < 6:
            night_sessions.add(row["session_id"])
            user_counts[user] += 1
            night_gpu_seconds += seconds
        if started.weekday() >= 5:
            weekend_sessions.add(row["session_id"])
            user_counts[user] += 1
            weekend_gpu_seconds += seconds
    return {
        "night_job_count": len(night_sessions),
        "weekend_job_count": len(weekend_sessions),
        "night_gpu_hours": round(night_gpu_seconds / 3600, 2),
        "weekend_gpu_hours": round(weekend_gpu_seconds / 3600, 2),
        "top_users": [
            {"user": user, "job_count": count}
            for user, count in user_counts.most_common(8)
        ],
    }


def _node_gpus(store: SQLiteStore, node_id: str) -> list[dict[str, Any]]:
    rows = store.connection.execute(
        """
        SELECT uuid, gpu_index, name, memory_total_mb
        FROM gpus
        WHERE node_id = ?
        ORDER BY gpu_index ASC
        """,
        (node_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _rollup_series(
    store: SQLiteStore,
    *,
    node_id: str,
    range_start: float,
    range_end: float,
    source_bucket: int,
    target_bucket: int,
) -> list[dict[str, Any]]:
    rows = _grouped_rollups(
        store,
        node_id=node_id,
        range_start=range_start,
        range_end=range_end,
        source_bucket=source_bucket,
        target_bucket=target_bucket,
    )
    by_gpu: dict[str, dict[str, Any]] = {}
    for row in rows:
        gpu = by_gpu.setdefault(
            row["gpu_uuid"],
            {
                "gpu_uuid": row["gpu_uuid"],
                "gpu_index": row["gpu_index"],
                "gpu_name": row["gpu_name"],
                "points": [],
            },
        )
        gpu["points"].append(_point(row))
    return sorted(by_gpu.values(), key=lambda item: (item["gpu_index"] is None, item["gpu_index"] or 0, item["gpu_uuid"]))


def _rollup_heatmap(
    store: SQLiteStore,
    *,
    node_id: str,
    range_start: float,
    range_end: float,
    source_bucket: int,
    target_bucket: int,
) -> list[dict[str, Any]]:
    rows = _grouped_rollups(
        store,
        node_id=node_id,
        range_start=range_start,
        range_end=range_end,
        source_bucket=source_bucket,
        target_bucket=target_bucket,
    )
    by_gpu: dict[str, dict[str, Any]] = {}
    for row in rows:
        gpu = by_gpu.setdefault(
            row["gpu_uuid"],
            {
                "gpu_uuid": row["gpu_uuid"],
                "gpu_index": row["gpu_index"],
                "gpu_name": row["gpu_name"],
                "buckets": [],
            },
        )
        gpu["buckets"].append(
            {
                "bucket_start": row["bucket_start"],
                "avg_gpu_utilization": round(row["avg_gpu_utilization"], 1),
                "max_gpu_utilization": round(row["max_gpu_utilization"], 1),
                "avg_memory_used_mb": round(row["avg_memory_used_mb"], 1),
                "sample_count": row["sample_count"],
            }
        )
    return sorted(by_gpu.values(), key=lambda item: (item["gpu_index"] is None, item["gpu_index"] or 0, item["gpu_uuid"]))


def _grouped_rollups(
    store: SQLiteStore,
    *,
    node_id: str,
    range_start: float,
    range_end: float,
    source_bucket: int,
    target_bucket: int,
) -> list[sqlite3.Row]:
    return store.connection.execute(
        """
        SELECT
          CAST(r.bucket_start / ? AS INTEGER) * ? AS bucket_start,
          r.gpu_uuid,
          g.gpu_index,
          g.name AS gpu_name,
          SUM(r.avg_gpu_utilization * r.sample_count) / SUM(r.sample_count)
            AS avg_gpu_utilization,
          MAX(r.max_gpu_utilization) AS max_gpu_utilization,
          SUM(r.avg_memory_used_mb * r.sample_count) / SUM(r.sample_count)
            AS avg_memory_used_mb,
          MAX(r.max_memory_used_mb) AS max_memory_used_mb,
          SUM(r.avg_power_watts * r.sample_count) / SUM(r.sample_count)
            AS avg_power_watts,
          MAX(r.max_power_watts) AS max_power_watts,
          SUM(r.avg_temperature_c * r.sample_count) / SUM(r.sample_count)
            AS avg_temperature_c,
          MAX(r.max_temperature_c) AS max_temperature_c,
          SUM(r.sample_count) AS sample_count
        FROM gpu_metric_rollups r
        LEFT JOIN gpus g ON g.node_id = r.node_id AND g.uuid = r.gpu_uuid
        WHERE r.bucket_seconds = ?
          AND r.node_id = ?
          AND r.bucket_start >= ?
          AND r.bucket_start <= ?
        GROUP BY CAST(r.bucket_start / ? AS INTEGER) * ?, r.gpu_uuid
        ORDER BY bucket_start ASC, g.gpu_index ASC
        """,
        (
            target_bucket,
            target_bucket,
            source_bucket,
            node_id,
            range_start,
            range_end,
            target_bucket,
            target_bucket,
        ),
    ).fetchall()


def _point(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "bucket_start": row["bucket_start"],
        "avg_gpu_utilization": round(row["avg_gpu_utilization"], 1),
        "max_gpu_utilization": round(row["max_gpu_utilization"], 1),
        "avg_memory_used_mb": round(row["avg_memory_used_mb"], 1),
        "max_memory_used_mb": row["max_memory_used_mb"],
        "avg_power_watts": round(row["avg_power_watts"], 1),
        "max_power_watts": round(row["max_power_watts"], 1),
        "avg_temperature_c": round(row["avg_temperature_c"], 1),
        "max_temperature_c": row["max_temperature_c"],
        "sample_count": row["sample_count"],
    }


def _meta(
    *,
    range_start: float,
    range_end: float,
    generated_at: float,
    bucket_seconds: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "enabled": True,
        "generated_at": generated_at,
        "range_start": range_start,
        "range_end": range_end,
        "timezone": TIMEZONE,
    }
    if bucket_seconds is not None:
        payload["bucket_seconds"] = bucket_seconds
    return payload


def _user_payload(item: UserUsage) -> dict[str, Any]:
    return {
        "user": item.user,
        "gpu_hours": round(item.gpu_hours, 2),
        "weighted_gpu_hours": round(item.weighted_gpu_hours, 2),
        "task_count": len(item.sessions),
        "job_count": len(item.jobs),
        "last_seen_at": item.last_seen_at,
        "top_gpu_models": [
            {"name": name, "gpu_hours": round(seconds / 3600, 2)}
            for name, seconds in item.gpu_model_seconds.most_common(3)
        ],
    }


def _job_payload(item: JobUsage) -> dict[str, Any]:
    return {
        "job_key": item.job_key,
        "user": item.user,
        "node_id": item.node_id,
        "task_name": item.task_name,
        "started_at": item.started_at,
        "last_seen_at": item.last_seen_at,
        "duration_seconds": max(0.0, item.last_seen_at - item.started_at),
        "gpu_count": len(item.gpu_uuids),
        "session_count": len(item.sessions),
        "gpu_hours": round(item.gpu_hours, 2),
        "weighted_gpu_hours": round(item.weighted_gpu_hours, 2),
        "status": item.status,
    }


def _top(items: Any, attr: str, limit: int) -> list[Any]:
    return sorted(items, key=lambda item: getattr(item, attr), reverse=True)[:limit]


def _range_seconds(name: str, *, default: str) -> int:
    return RANGES.get(name, RANGES[default])


def _select_rollup_bucket(*, range_start: float, range_end: float) -> int:
    span = range_end - range_start
    if span <= 7 * 24 * 60 * 60:
        return ROLLUP_20S
    if span <= 60 * 24 * 60 * 60:
        return ROLLUP_2M
    return ROLLUP_1H


def _target_bucket(span: float, source_bucket: int, *, target_points: int) -> int:
    wanted = max(source_bucket, math.ceil(span / target_points))
    return int(math.ceil(wanted / source_bucket) * source_bucket)


def _heatmap_bucket(span: float) -> int:
    if span <= 24 * 60 * 60:
        return 30 * 60
    if span <= 7 * 24 * 60 * 60:
        return 2 * 60 * 60
    return 4 * 60 * 60


def compact_gpu_name(name: str | None) -> str:
    return (name or "unknown").replace("NVIDIA ", "")


def _value(row: sqlite3.Row | dict[str, Any], key: str) -> Any:
    return row[key]
