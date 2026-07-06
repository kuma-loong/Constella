from __future__ import annotations

import math
import sqlite3
import time
import asyncio
from array import array
from dataclasses import dataclass, field
from typing import Any

from .analytics import job_key
from .db import ROLLUP_20S, SQLiteStore
from .schema import GpuInfo, NodeSnapshot

HIGHRES_RETENTION_SECONDS = 2 * 60 * 60
HIGHRES_MAX_JOB_SECONDS = 60 * 60
HIGHRES_JOB_LOOKBACK_SECONDS = 7 * 24 * 60 * 60
HIGHRES_MIN_INTERVAL_SECONDS = 0.5
HIGHRES_DEFAULT_PADDING_SECONDS = 20.0


@dataclass(slots=True)
class GpuSampleRing:
    capacity: int
    timestamps: array = field(init=False)
    gpu_utilization: array = field(init=False)
    mem_utilization: array = field(init=False)
    memory_used_mb: array = field(init=False)
    memory_total_mb: array = field(init=False)
    power_watts: array = field(init=False)
    temperature_c: array = field(init=False)
    write_index: int = 0
    count: int = 0

    def __post_init__(self) -> None:
        self.timestamps = array("d", [0.0]) * self.capacity
        self.gpu_utilization = array("f", [0.0]) * self.capacity
        self.mem_utilization = array("f", [0.0]) * self.capacity
        self.memory_used_mb = array("f", [0.0]) * self.capacity
        self.memory_total_mb = array("f", [0.0]) * self.capacity
        self.power_watts = array("f", [0.0]) * self.capacity
        self.temperature_c = array("f", [0.0]) * self.capacity

    def append(self, *, sampled_at: float, gpu: GpuInfo) -> None:
        index = self.write_index
        self.timestamps[index] = sampled_at
        self.gpu_utilization[index] = float(gpu.utilization_gpu)
        self.mem_utilization[index] = float(gpu.utilization_mem)
        self.memory_used_mb[index] = float(gpu.memory_used_mb)
        self.memory_total_mb[index] = float(gpu.memory_total_mb)
        self.power_watts[index] = float(gpu.power_watts)
        self.temperature_c[index] = float(gpu.temperature_c)
        self.write_index = (index + 1) % self.capacity
        self.count = min(self.capacity, self.count + 1)

    @property
    def oldest_at(self) -> float | None:
        if self.count == 0:
            return None
        return float(self.timestamps[self._chronological_index(0)])

    @property
    def newest_at(self) -> float | None:
        if self.count == 0:
            return None
        return float(self.timestamps[self._chronological_index(self.count - 1)])

    def points(self, *, since: float, until: float) -> list[dict[str, float]]:
        items: list[dict[str, float]] = []
        for offset in range(self.count):
            index = self._chronological_index(offset)
            sampled_at = float(self.timestamps[index])
            if sampled_at < since:
                continue
            if sampled_at > until:
                break
            items.append(
                {
                    "sampled_at": sampled_at,
                    "bucket_start": sampled_at,
                    "utilization_gpu": round(float(self.gpu_utilization[index]), 1),
                    "utilization_mem": round(float(self.mem_utilization[index]), 1),
                    "memory_used_mb": round(float(self.memory_used_mb[index]), 1),
                    "memory_total_mb": round(float(self.memory_total_mb[index]), 1),
                    "power_watts": round(float(self.power_watts[index]), 1),
                    "temperature_c": round(float(self.temperature_c[index]), 1),
                    "avg_gpu_utilization": round(float(self.gpu_utilization[index]), 1),
                    "avg_memory_used_mb": round(float(self.memory_used_mb[index]), 1),
                    "avg_power_watts": round(float(self.power_watts[index]), 1),
                    "avg_temperature_c": round(float(self.temperature_c[index]), 1),
                }
            )
        return items

    def observed_interval_seconds(self) -> float | None:
        if self.count < 2:
            return None
        newest = self.newest_at
        oldest = self.oldest_at
        if newest is None or oldest is None or newest <= oldest:
            return None
        return (newest - oldest) / (self.count - 1)

    def _chronological_index(self, offset: int) -> int:
        if self.count < self.capacity:
            return offset
        return (self.write_index + offset) % self.capacity


class HighresGpuCache:
    def __init__(
        self,
        *,
        retention_seconds: float = HIGHRES_RETENTION_SECONDS,
        min_interval_seconds: float = HIGHRES_MIN_INTERVAL_SECONDS,
    ):
        self.retention_seconds = float(retention_seconds)
        self.capacity = int(math.ceil(self.retention_seconds / min_interval_seconds))
        self.rings: dict[tuple[str, str], GpuSampleRing] = {}
        self.sample_count = 0
        self.dropped_samples = 0
        self.last_sample_at: float | None = None

    def add_snapshot(self, snapshot: NodeSnapshot) -> None:
        sampled_at = float(snapshot.sampled_at)
        for gpu in snapshot.gpus:
            key = (snapshot.node_id, gpu.uuid)
            ring = self.rings.get(key)
            if ring is None:
                ring = GpuSampleRing(capacity=self.capacity)
                self.rings[key] = ring
            ring.append(sampled_at=sampled_at, gpu=gpu)
            self.sample_count += 1
        self.last_sample_at = sampled_at

    def add_sample_message(self, message: dict[str, Any]) -> None:
        node_id = str(message.get("node_id") or "")
        sampled_at = float(message.get("sampled_at") or 0.0)
        if not node_id or sampled_at <= 0:
            self.dropped_samples += 1
            return
        for raw_gpu in message.get("gpus") or []:
            gpu = GpuInfo(
                index=int(raw_gpu.get("gpu_index") or raw_gpu.get("index") or 0),
                node_id=node_id,
                uuid=str(raw_gpu.get("uuid") or "unknown"),
                name=str(raw_gpu.get("name") or "unknown"),
                utilization_gpu=int(raw_gpu.get("utilization_gpu") or 0),
                utilization_mem=int(raw_gpu.get("utilization_mem") or 0),
                memory_total_mb=int(raw_gpu.get("memory_total_mb") or 0),
                memory_used_mb=int(raw_gpu.get("memory_used_mb") or 0),
                power_watts=float(raw_gpu.get("power_watts") or 0.0),
                temperature_c=int(raw_gpu.get("temperature_c") or 0),
            )
            key = (node_id, gpu.uuid)
            ring = self.rings.get(key)
            if ring is None:
                ring = GpuSampleRing(capacity=self.capacity)
                self.rings[key] = ring
            ring.append(sampled_at=sampled_at, gpu=gpu)
            self.sample_count += 1
        self.last_sample_at = sampled_at

    def series_for(
        self,
        *,
        node_id: str,
        gpu_uuid: str,
        since: float,
        until: float,
    ) -> tuple[GpuSampleRing | None, list[dict[str, float]]]:
        ring = self.rings.get((node_id, gpu_uuid))
        if ring is None:
            return None, []
        return ring, ring.points(since=since, until=until)

    def status(self) -> dict[str, Any]:
        oldest = [ring.oldest_at for ring in self.rings.values() if ring.oldest_at is not None]
        newest = [ring.newest_at for ring in self.rings.values() if ring.newest_at is not None]
        valid_points = sum(ring.count for ring in self.rings.values())
        bytes_per_point = 8 + 6 * 4
        return {
            "enabled": True,
            "ring_count": len(self.rings),
            "capacity_per_gpu": self.capacity,
            "valid_point_count": valid_points,
            "approx_bytes": valid_points * bytes_per_point,
            "retention_seconds": self.retention_seconds,
            "sample_count": self.sample_count,
            "dropped_samples": self.dropped_samples,
            "oldest_sample_at": min(oldest) if oldest else None,
            "newest_sample_at": max(newest) if newest else None,
            "last_sample_at": self.last_sample_at,
        }


def query_jobs(
    store: SQLiteStore,
    *,
    q: str | None = None,
    user: str | None = None,
    pid: int | None = None,
    node_id: str | None = None,
    status: str | None = None,
    since: float | None = None,
    until: float | None = None,
    max_duration_seconds: float | None = None,
    recent_seconds: float | None = HIGHRES_JOB_LOOKBACK_SECONDS,
    limit: int = 100,
    now: float | None = None,
) -> list[dict[str, Any]]:
    current_time = time.time() if now is None else now
    range_start = since
    if recent_seconds is not None:
        recent_start = current_time - max(0.0, recent_seconds)
        range_start = recent_start if range_start is None else max(range_start, recent_start)
    rows = _job_rows(store, range_start=range_start, range_end=until)
    jobs = _group_jobs(rows)
    filtered = [
        job
        for job in jobs.values()
        if _job_matches(
            job,
            q=q,
            user=user,
            pid=pid,
            node_id=node_id,
            status=status,
            max_duration_seconds=max_duration_seconds,
        )
    ]
    filtered.sort(key=lambda item: item["last_seen_at"], reverse=True)
    return filtered[: max(1, min(limit, 500))]


def get_job(
    store: SQLiteStore,
    key: str,
    *,
    now: float | None = None,
    lookback_seconds: float | None = HIGHRES_JOB_LOOKBACK_SECONDS,
) -> dict[str, Any] | None:
    current_time = time.time() if now is None else now
    range_start = (
        current_time - max(0.0, lookback_seconds)
        if lookback_seconds is not None
        else None
    )
    return _group_jobs(_job_rows(store, range_start=range_start, range_end=None)).get(key)


def job_curve(
    store: SQLiteStore,
    cache: HighresGpuCache,
    *,
    key: str,
    padding_seconds: float = HIGHRES_DEFAULT_PADDING_SECONDS,
    now: float | None = None,
) -> dict[str, Any] | None:
    job = get_job(store, key, now=now)
    if job is None:
        return None
    padding = max(0.0, min(float(padding_seconds), 300.0))
    range_start = max(0.0, float(job["started_at"]) - padding)
    range_end = float(job["last_seen_at"]) + padding
    duration = max(0.0, float(job["duration_seconds"]))
    warnings: list[str] = []
    if duration < HIGHRES_MAX_JOB_SECONDS:
        highres = _highres_curve(cache, job=job, range_start=range_start, range_end=range_end)
        if highres is not None:
            return {
                **highres,
                "enabled": True,
                "job": job,
                "job_key": key,
                "range_start": range_start,
                "range_end": range_end,
                "cache_retention_seconds": cache.retention_seconds,
                "expired": False,
                "warnings": warnings,
            }
        warnings.append("high-resolution cache does not cover the full job window")
    else:
        warnings.append("job duration is 1 hour or longer, using rollup history")
    rollup = _rollup_curve(store, job=job, range_start=range_start, range_end=range_end)
    return {
        "enabled": True,
        "source": "rollup",
        "job": job,
        "job_key": key,
        "range_start": range_start,
        "range_end": range_end,
        "coverage_start": _series_min_time(rollup),
        "coverage_end": _series_max_time(rollup),
        "cache_retention_seconds": cache.retention_seconds,
        "resolution_seconds": ROLLUP_20S,
        "expired": True,
        "warnings": warnings,
        "series": rollup,
    }


def _job_rows(
    store: SQLiteStore,
    *,
    range_start: float | None,
    range_end: float | None,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if range_start is not None:
        clauses.append("s.last_seen_at >= ?")
        params.append(range_start)
    if range_end is not None:
        clauses.append("s.first_seen_at <= ?")
        params.append(range_end)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return store.connection.execute(
        f"""
        SELECT
          s.session_id, s.node_id, s.pid, s.ppid, s.process_start_time,
          s.parent_start_time, s.user, s.task_name, s.process_name, s.exe,
          s.cmdline_hash, s.cmdline_text, s.first_seen_at, s.last_seen_at,
          s.duration_seconds, s.status, s.sample_count,
          u.gpu_uuid, u.first_seen_at AS gpu_first_seen_at,
          u.last_seen_at AS gpu_last_seen_at, u.max_memory_mb, u.avg_memory_mb,
          g.gpu_index, g.name AS gpu_name, g.memory_total_mb
        FROM process_sessions s
        JOIN process_gpu_usages u ON u.session_id = s.session_id
        LEFT JOIN gpus g ON g.node_id = u.node_id AND g.uuid = u.gpu_uuid
        {where}
        ORDER BY s.last_seen_at DESC
        """,
        params,
    ).fetchall()


def _group_jobs(rows: list[sqlite3.Row]) -> dict[str, dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = job_key(row)
        user = row["user"] or "unknown"
        job = jobs.get(key)
        if job is None:
            job = {
                "job_key": key,
                "node_id": row["node_id"],
                "user": user,
                "task_name": row["task_name"],
                "started_at": float(row["first_seen_at"]),
                "last_seen_at": float(row["last_seen_at"]),
                "duration_seconds": 0.0,
                "status": row["status"],
                "sessions": {},
                "pids": set(),
                "gpus": {},
                "search_text": "",
            }
            jobs[key] = job
        job["started_at"] = min(float(job["started_at"]), float(row["first_seen_at"]))
        job["last_seen_at"] = max(float(job["last_seen_at"]), float(row["last_seen_at"]))
        if row["status"] == "running":
            job["status"] = "running"
        session = job["sessions"].setdefault(
            row["session_id"],
            {
                "session_id": row["session_id"],
                "pid": row["pid"],
                "ppid": row["ppid"],
                "task_name": row["task_name"],
                "process_name": row["process_name"],
                "exe": row["exe"],
                "cmdline_text": row["cmdline_text"],
                "started_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "status": row["status"],
            },
        )
        session["last_seen_at"] = max(float(session["last_seen_at"]), float(row["last_seen_at"]))
        if row["pid"] is not None:
            job["pids"].add(int(row["pid"]))
        gpu_key = (row["node_id"], row["gpu_uuid"])
        job["gpus"][gpu_key] = {
            "node_id": row["node_id"],
            "gpu_uuid": row["gpu_uuid"],
            "gpu_index": row["gpu_index"],
            "gpu_name": row["gpu_name"],
            "memory_total_mb": row["memory_total_mb"],
        }
        job["search_text"] += " ".join(
            str(value or "")
            for value in (
                row["task_name"],
                row["process_name"],
                row["exe"],
                row["cmdline_text"],
                row["user"],
                row["pid"],
            )
        ).lower()
    return {key: _finalize_job(job) for key, job in jobs.items()}


def _finalize_job(job: dict[str, Any]) -> dict[str, Any]:
    job["duration_seconds"] = max(0.0, float(job["last_seen_at"]) - float(job["started_at"]))
    job["sessions"] = sorted(job["sessions"].values(), key=lambda item: item["started_at"])
    job["pids"] = sorted(job["pids"])
    job["gpus"] = sorted(
        job["gpus"].values(),
        key=lambda item: (
            item["node_id"],
            item["gpu_index"] is None,
            item["gpu_index"] or 0,
            item["gpu_uuid"],
        ),
    )
    job["gpu_count"] = len(job["gpus"])
    job["session_count"] = len(job["sessions"])
    job.pop("search_text", None)
    return job


def _job_matches(
    job: dict[str, Any],
    *,
    q: str | None,
    user: str | None,
    pid: int | None,
    node_id: str | None,
    status: str | None,
    max_duration_seconds: float | None,
) -> bool:
    if user and job["user"] != user:
        return False
    if node_id and job["node_id"] != node_id:
        return False
    if status and job["status"] != status:
        return False
    if pid is not None and int(pid) not in set(job["pids"]):
        return False
    if max_duration_seconds is not None and job["duration_seconds"] > max_duration_seconds:
        return False
    if q:
        needle = q.strip().lower()
        if needle and not any(
            needle in " ".join(
                str(session.get(key) or "")
                for key in ("task_name", "process_name", "exe", "cmdline_text", "pid")
            ).lower()
            for session in job["sessions"]
        ) and needle not in str(job.get("user") or "").lower():
            return False
    return True


def _highres_curve(
    cache: HighresGpuCache,
    *,
    job: dict[str, Any],
    range_start: float,
    range_end: float,
) -> dict[str, Any] | None:
    series: list[dict[str, Any]] = []
    coverage_start: float | None = None
    coverage_end: float | None = None
    intervals: list[float] = []
    for gpu in job["gpus"]:
        ring, points = cache.series_for(
            node_id=gpu["node_id"],
            gpu_uuid=gpu["gpu_uuid"],
            since=range_start,
            until=range_end,
        )
        if ring is None or not points:
            return None
        oldest = ring.oldest_at
        newest = ring.newest_at
        if oldest is None or newest is None or oldest > range_start or newest < range_end:
            return None
        interval = ring.observed_interval_seconds()
        if interval is not None:
            intervals.append(interval)
        coverage_start = oldest if coverage_start is None else min(coverage_start, oldest)
        coverage_end = newest if coverage_end is None else max(coverage_end, newest)
        series.append({**gpu, "label": _gpu_label(gpu), "points": points})
    return {
        "source": "high_res_memory",
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "resolution_seconds": min(intervals) if intervals else None,
        "series": series,
    }


def _rollup_curve(
    store: SQLiteStore,
    *,
    job: dict[str, Any],
    range_start: float,
    range_end: float,
) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for gpu in job["gpus"]:
        points = store.query_gpu_history(
            node_id=gpu["node_id"],
            gpu_uuid=gpu["gpu_uuid"],
            since=range_start,
            until=range_end,
            limit=5000,
        )
        series.append({**gpu, "label": _gpu_label(gpu), "points": points})
    return series


def _series_min_time(series: list[dict[str, Any]]) -> float | None:
    values = [point["sampled_at"] for item in series for point in item["points"]]
    return min(values) if values else None


def _series_max_time(series: list[dict[str, Any]]) -> float | None:
    values = [point["sampled_at"] for item in series for point in item["points"]]
    return max(values) if values else None


def _gpu_label(gpu: dict[str, Any]) -> str:
    index = gpu.get("gpu_index")
    suffix = f"GPU{index}" if index is not None else gpu["gpu_uuid"]
    return f"{gpu['node_id']} {suffix}"


def gpu_sample_message(snapshot: NodeSnapshot) -> dict[str, Any]:
    return {
        "type": "gpu_sample",
        "node_id": snapshot.node_id,
        "sampled_at": snapshot.sampled_at,
        "refresh_interval": snapshot.refresh_interval,
        "gpus": [
            {
                "uuid": gpu.uuid,
                "gpu_index": gpu.index,
                "name": gpu.name,
                "utilization_gpu": gpu.utilization_gpu,
                "utilization_mem": gpu.utilization_mem,
                "memory_used_mb": gpu.memory_used_mb,
                "memory_total_mb": gpu.memory_total_mb,
                "power_watts": gpu.power_watts,
                "temperature_c": gpu.temperature_c,
            }
            for gpu in snapshot.gpus
        ],
    }


class HighresSampleBroadcaster:
    def __init__(self, *, queue_size: int = 256):
        self.queue_size = max(1, int(queue_size))
        self.queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self.published_messages = 0
        self.dropped_messages = 0

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.queue_size)
        self.queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.queues.discard(queue)

    def publish_snapshot(self, snapshot: NodeSnapshot) -> None:
        message = gpu_sample_message(snapshot)
        self.published_messages += 1
        for queue in list(self.queues):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self.dropped_messages += 1

    def status(self) -> dict[str, Any]:
        return {
            "subscriber_count": len(self.queues),
            "published_messages": self.published_messages,
            "dropped_messages": self.dropped_messages,
            "queue_size": self.queue_size,
        }
