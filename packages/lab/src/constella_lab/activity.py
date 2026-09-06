"""Bounded, read-only personal activity queries over existing session records."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
import sys
import time
from collections import OrderedDict, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from .activity_usage import (
    TIMEZONE,
    Usage,
    model_statistics,
    resolve_usage,
    segments,
    summary,
    union,
)
from .store import LabStore

MAX_ROWS = 100_000
MAX_CACHE_BYTES = 16 * 1024 * 1024
CACHE_SECONDS = 60


@dataclass
class ActivityData:
    records: list[Usage]
    as_of: float
    start: float
    days: list[tuple[str, float, float]]
    eligible: list[tuple[float, float]]
    missing_uid: int
    size: int


def read_activity(
    path: Path, bindings: list[dict[str, Any]], user_id: str, period: int
) -> ActivityData:
    now = time.time()
    today = datetime.fromtimestamp(now, TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    first = today - timedelta(days=period - 1)
    start = first.timestamp()
    days = [
        (
            (first + timedelta(days=i)).date().isoformat(),
            (first + timedelta(days=i)).timestamp(),
            min(now, (first + timedelta(days=i + 1)).timestamp()),
        )
        for i in range(period)
    ]
    eligible = union(
        (max(start, b["valid_from"]), min(now, b["valid_to"] or now)) for b in bindings
    )
    nodes: dict[str, set[int]] = defaultdict(set)
    for binding in bindings:
        nodes[binding["node_id"]].add(binding["unix_uid"])
    rows: list[dict[str, Any]] = []
    if nodes:
        # Do not call SQLiteStore.open(): that initializer can run schema maintenance.
        with closing(
            sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
        ) as con:
            con.row_factory = sqlite3.Row
            con.execute("BEGIN")
            deadline = time.monotonic() + 2
            con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            for node, uids in nodes.items():
                placeholders = ",".join("?" for _ in uids)
                records = con.execute(
                    f"""
                    SELECT s.session_id, s.node_id, s.user, s.user_uid, s.pid, s.ppid,
                           s.process_start_time, s.parent_start_time, s.task_name, s.status,
                           s.first_seen_at AS session_first_seen_at,
                           s.last_seen_at AS session_last_seen_at,
                           u.first_seen_at, u.last_seen_at, u.gpu_uuid,
                           g.name AS gpu_name, g.gpu_index, g.device_type, g.card_id
                    FROM process_gpu_usages u
                    JOIN process_sessions s ON s.session_id = u.session_id
                    LEFT JOIN gpus g ON g.node_id = u.node_id AND g.uuid = u.gpu_uuid
                    WHERE u.node_id = ? AND u.first_seen_at <= ? AND u.last_seen_at >= ?
                      AND (s.user_uid IN ({placeholders}) OR s.user_uid IS NULL)
                    LIMIT ?
                """,
                    (node, now, start, *sorted(uids), MAX_ROWS + 1 - len(rows)),
                ).fetchall()
                rows.extend(dict(r) for r in records)
                if len(rows) > MAX_ROWS:
                    raise ValueError("activity_query_too_large")
    records = [r for r in resolve_usage(rows, bindings, start, now) if r.owner == f"user:{user_id}"]
    return ActivityData(
        records,
        now,
        start,
        days,
        eligible,
        len({r["session_id"] for r in rows if r["user_uid"] is None}),
        sum(
            128
            + sys.getsizeof(r)
            + sys.getsizeof(r.row)
            + sum(sys.getsizeof(v) for v in r.row.values())
            + sum(sys.getsizeof(v) for v in (r.owner, r.label, r.card, r.model, r.key))
            for r in records
        )
        + sys.getsizeof(records)
        + 4096,
    )


class ActivityService:
    def __init__(self) -> None:
        self.cache: OrderedDict[str, ActivityData] = OrderedDict()
        self.pending: dict[str, asyncio.Task[ActivityData]] = {}
        self.workers = asyncio.Semaphore(2)

    async def get(
        self, path: Path, bindings: list[dict[str, Any]], user_id: str, period: int
    ) -> ActivityData:
        fingerprint = json.dumps(bindings, sort_keys=True, default=str)
        day = datetime.now(TIMEZONE).date().isoformat()
        key = hashlib.sha256(f"{path}:{user_id}:{period}:{day}:{fingerprint}".encode()).hexdigest()
        cached = self.cache.get(key)
        if cached and time.time() - cached.as_of < CACHE_SECONDS:
            self.cache.move_to_end(key)
            return cached
        if key not in self.pending:
            if len(self.pending) >= 16:
                raise HTTPException(503, detail="activity_busy")

            async def load() -> ActivityData:
                try:
                    async with self.workers:
                        data = await asyncio.to_thread(
                            read_activity, path, bindings, user_id, period
                        )
                    if data.size <= MAX_CACHE_BYTES:
                        self.cache[key] = data
                        self.cache.move_to_end(key)
                        while (
                            len(self.cache) > 16
                            or sum(d.size for d in self.cache.values()) > MAX_CACHE_BYTES
                        ):
                            self.cache.popitem(last=False)
                    return data
                finally:
                    self.pending.pop(key, None)

            self.pending[key] = asyncio.create_task(load())
        return await asyncio.shield(self.pending[key])


def current_status(job: dict[str, Any], cluster: Any, now: float) -> str:
    runtime = cluster.latest_by_node.get(job["node_id"])
    if runtime:
        snap = runtime.snapshot
        fresh = (
            runtime.connected
            and snap.status == "online"
            and now - snap.sampled_at < max(30, 3 * snap.process_interval)
        )
        if fresh:
            identities = {(s["pid"], s["process_start_time"]) for s in job["sessions"]}
            if any(
                (p.pid, p.process_start_time) in identities
                for gpu in snap.gpus
                for p in gpu.processes
            ):
                return "running"
            # Aggregated or failed process listings cannot establish that a process ended.
            if (
                not snap.gpus
                or snap.error
                or any(gpu.other_users or gpu.error for gpu in snap.gpus)
            ):
                return "unknown"
            return "ended"
    return "ended" if job["status"] == "ended" else "unknown"


def activity_jobs(records: list[Usage], cluster: Any) -> list[dict[str, Any]]:
    groups: dict[str, list[Usage]] = defaultdict(list)
    for record in records:
        groups[record.key].append(record)
    jobs = []
    now = time.time()
    for key, items in groups.items():
        row = items[0].row
        sessions = {
            r.row["session_id"]: {
                "pid": r.row["pid"],
                "process_start_time": r.row["process_start_time"],
            }
            for r in items
        }
        job = {
            "job_key": key,
            "node_id": row["node_id"],
            "user": row["user"],
            "task_name": row["task_name"],
            "pids": sorted({r.row["pid"] for r in items}),
            "sessions": list(sessions.values()),
            "started_at": min(r.row["session_first_seen_at"] for r in items),
            "last_seen_at": max(r.row["session_last_seen_at"] for r in items),
            "status": "running" if any(r.row["status"] == "running" for r in items) else "ended",
            "models": sorted({r.model for r in items}),
            "gpu_count": len({r.card for r in items}),
            "segments": segments(items, min(r.start for r in items), max(r.end for r in items)),
            "intervals": [
                {"start": r.start, "end": r.end, "model": r.model, "card": r.card} for r in items
            ],
        }
        job["status"] = current_status(job, cluster, now)
        jobs.append(job)
    return sorted(jobs, key=lambda j: (j["status"] != "running", -j["last_seen_at"], j["job_key"]))


def busiest_hours(data: ActivityData, records: list[Usage]) -> dict[str, int] | None:
    usage = [0.0] * 24
    exposure = [0.0] * 24
    spans = segments(records, data.start, data.as_of)
    for _, start, end in data.days:
        for hour in range(24):
            a, b = start + hour * 3600, min(end, start + (hour + 1) * 3600)
            if a >= b:
                continue
            usage[hour] += sum(
                max(0, min(b, s["end"]) - max(a, s["start"])) * s["count"] for s in spans
            )
            exposure[hour] += sum(max(0, min(b, y) - max(a, x)) for x, y in data.eligible)
    averages = [u / e if e else 0 for u, e in zip(usage, exposure)]
    if not any(averages):
        return None
    peak = max(range(24), key=lambda h: sum(averages[(h + i) % 24] for i in range(3)))
    return {"start_hour": peak, "end_hour": (peak + 3) % 24}


def payload(
    data: ActivityData,
    cluster: Any,
    *,
    date: str | None,
    model: str,
    status: str,
    q: str,
    cursor: str | None,
    limit: int,
) -> dict[str, Any]:
    if date and date not in {day[0] for day in data.days}:
        raise HTTPException(422, detail="date_outside_period")
    selected = [r for r in data.records if not model or r.model == model]
    days = [
        {
            "date": label,
            "start": start,
            "end": end,
            **summary(selected, start, end),
            "segments": segments(selected, start, end),
        }
        for label, start, end in data.days
    ]
    day = next((d for d in days if d["date"] == date), None)
    jobs = activity_jobs(data.records, cluster)
    query = q.strip().casefold()
    matching = [
        j
        for j in jobs
        if (status == "all" or j["status"] == status)
        and (
            not query
            or query
            in f"{j['task_name']} {j['node_id']} {j['user']} {' '.join(map(str, j['pids']))}".casefold()
        )
        and (
            j["status"] == "running"
            or any(
                (not model or r["model"] == model)
                and (
                    not day
                    or r["start"] < day["end"]
                    and (r["end"] > day["start"] or r["start"] == r["end"] == day["start"])
                )
                for r in j["intervals"]
            )
        )
    ]
    signature = hashlib.sha256(
        json.dumps([data.as_of, date, model, status, q, [j["job_key"] for j in matching]]).encode()
    ).hexdigest()[:20]
    offset = 0
    if cursor:
        try:
            token, offset = json.loads(base64.urlsafe_b64decode(cursor).decode())
            if token != signature:
                raise HTTPException(409, detail="activity_page_expired")
            if not isinstance(offset, int) or offset < 0 or offset > len(matching):
                raise ValueError("invalid offset")
        except (ValueError, TypeError, UnicodeError) as exc:
            raise HTTPException(422, detail="invalid_cursor") from exc
    following = offset + limit
    next_cursor = (
        base64.urlsafe_b64encode(json.dumps([signature, following]).encode()).decode()
        if following < len(matching)
        else None
    )
    # Day tracks are paged independently from the overall totals; do not disguise a limit as complete data.
    tracks = activity_jobs(
        [
            r
            for r in selected
            if day
            and r.start < day["end"]
            and (r.end > day["start"] or r.start == r.end == day["start"])
        ],
        cluster,
    )
    return {
        "enabled": True,
        "as_of": data.as_of,
        "range_start": data.start,
        "timezone": str(TIMEZONE),
        "accuracy": "observed_intervals",
        "coverage": "unknown",
        "missing_uid_sessions": data.missing_uid,
        "summary": summary(data.records, data.start, data.as_of),
        "selection": summary(
            selected, day["start"] if day else data.start, day["end"] if day else data.as_of
        ),
        "days": days,
        "gpu_models": model_statistics(data.records, data.start, data.as_of),
        "busiest_hours": busiest_hours(data, selected),
        "jobs": {
            "items": matching[offset:following],
            "next_cursor": next_cursor,
            "total": len(matching),
        },
        "day_jobs": {"items": tracks[:100], "total": len(tracks)},
    }


def build_activity_router(store: LabStore) -> APIRouter:
    router = APIRouter()
    service = ActivityService()

    @router.get("/api/lab/me/activity")
    async def activity(
        request: Request,
        range: str = Query("7d", pattern="^(7d|30d)$"),
        date: str | None = None,
        gpu_model: str = "",
        status: str = Query("all", pattern="^(all|running|ended|unknown)$"),
        q: str = Query("", max_length=200),
        cursor: str | None = Query(None, max_length=500),
        limit: int = Query(20, ge=1, le=100),
    ) -> dict[str, Any]:
        user_id = request.state.lab_user["id"]
        bindings = store.list_bindings(user_id=user_id, active_only=False)
        sink = request.app.state.db_sink
        if sink is None:
            return {"enabled": False, "availability": "history_disabled"}
        try:
            data = await service.get(sink.store.path, bindings, user_id, int(range[:-1]))
            async with service.workers:
                result = await asyncio.to_thread(
                    payload,
                    data,
                    request.app.state.cluster_state,
                    date=date,
                    model=gpu_model,
                    status=status,
                    q=q,
                    cursor=cursor,
                    limit=limit,
                )
            return {**result, "availability": "ready" if bindings else "unbound"}
        except (sqlite3.Error, ValueError) as exc:
            raise HTTPException(503, detail="activity_temporarily_unavailable") from exc

    return router
