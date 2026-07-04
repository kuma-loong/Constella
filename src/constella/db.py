from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import GpuInfo, GpuProcess, NodeSnapshot, process_session_id

RAW_SNAPSHOT_RETENTION_SECONDS = 12 * 60 * 60
ROLLUP_20S = 20
ROLLUP_2M = 120
ROLLUP_1H = 3600
ROLLUP_RETENTION_SECONDS = {
    ROLLUP_20S: 7 * 24 * 60 * 60,
    ROLLUP_2M: 60 * 24 * 60 * 60,
    ROLLUP_1H: 365 * 24 * 60 * 60,
}
ROLLUP_SOURCE_BUCKETS = {
    ROLLUP_2M: ROLLUP_20S,
    ROLLUP_1H: ROLLUP_2M,
}


@dataclass(slots=True)
class SQLiteSinkConfig:
    path: Path
    queue_size: int = 1024
    raw_snapshot_interval: float = 0.0


@dataclass(slots=True)
class RollupBucket:
    bucket_start: float
    node_id: str
    gpu_uuid: str
    sum_gpu_utilization: float = 0.0
    max_gpu_utilization: float = 0.0
    sum_memory_used_mb: float = 0.0
    max_memory_used_mb: int = 0
    sum_power_watts: float = 0.0
    max_power_watts: float = 0.0
    sum_temperature_c: float = 0.0
    max_temperature_c: int = 0
    sample_count: int = 0

    def add_gpu(self, gpu: GpuInfo) -> None:
        self.sample_count += 1
        self.sum_gpu_utilization += float(gpu.utilization_gpu)
        self.max_gpu_utilization = max(self.max_gpu_utilization, float(gpu.utilization_gpu))
        self.sum_memory_used_mb += float(gpu.memory_used_mb)
        self.max_memory_used_mb = max(self.max_memory_used_mb, int(gpu.memory_used_mb))
        self.sum_power_watts += float(gpu.power_watts)
        self.max_power_watts = max(self.max_power_watts, float(gpu.power_watts))
        self.sum_temperature_c += float(gpu.temperature_c)
        self.max_temperature_c = max(self.max_temperature_c, int(gpu.temperature_c))

    def to_row(self, bucket_seconds: int) -> dict[str, Any]:
        count = max(1, self.sample_count)
        return {
            "bucket_start": self.bucket_start,
            "bucket_seconds": bucket_seconds,
            "node_id": self.node_id,
            "gpu_uuid": self.gpu_uuid,
            "avg_gpu_utilization": self.sum_gpu_utilization / count,
            "max_gpu_utilization": self.max_gpu_utilization,
            "avg_memory_used_mb": self.sum_memory_used_mb / count,
            "max_memory_used_mb": self.max_memory_used_mb,
            "avg_power_watts": self.sum_power_watts / count,
            "max_power_watts": self.max_power_watts,
            "avg_temperature_c": self.sum_temperature_c / count,
            "max_temperature_c": self.max_temperature_c,
            "sample_count": self.sample_count,
        }


class SQLiteStore:
    def __init__(self, path: Path):
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.initialize()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def initialize(self) -> None:
        con = self._con()
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes (
              node_id TEXT PRIMARY KEY,
              hostname TEXT NOT NULL,
              display_name TEXT,
              first_seen_at REAL NOT NULL,
              last_seen_at REAL NOT NULL,
              agent_version TEXT,
              status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS gpus (
              gpu_id TEXT PRIMARY KEY,
              node_id TEXT NOT NULL,
              uuid TEXT NOT NULL,
              gpu_index INTEGER NOT NULL,
              pci_bus_id TEXT,
              name TEXT NOT NULL,
              memory_total_mb INTEGER NOT NULL,
              first_seen_at REAL NOT NULL,
              last_seen_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS gpu_metric_samples (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              sampled_at REAL NOT NULL,
              node_id TEXT NOT NULL,
              gpu_uuid TEXT NOT NULL,
              utilization_gpu REAL NOT NULL,
              utilization_mem REAL NOT NULL,
              memory_used_mb INTEGER NOT NULL,
              memory_total_mb INTEGER NOT NULL,
              power_watts REAL NOT NULL,
              power_limit_watts REAL NOT NULL,
              temperature_c INTEGER NOT NULL,
              sample_count INTEGER NOT NULL DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_gpu_metric_samples_lookup
              ON gpu_metric_samples(node_id, gpu_uuid, sampled_at);

            CREATE TABLE IF NOT EXISTS gpu_metric_rollups (
              bucket_start REAL NOT NULL,
              bucket_seconds INTEGER NOT NULL,
              node_id TEXT NOT NULL,
              gpu_uuid TEXT NOT NULL,
              avg_gpu_utilization REAL NOT NULL,
              max_gpu_utilization REAL NOT NULL,
              avg_memory_used_mb REAL NOT NULL,
              max_memory_used_mb INTEGER NOT NULL,
              avg_power_watts REAL NOT NULL,
              max_power_watts REAL NOT NULL,
              avg_temperature_c REAL NOT NULL,
              max_temperature_c INTEGER NOT NULL,
              sample_count INTEGER NOT NULL,
              PRIMARY KEY(bucket_start, bucket_seconds, node_id, gpu_uuid)
            );

            CREATE TABLE IF NOT EXISTS process_sessions (
              session_id TEXT PRIMARY KEY,
              node_id TEXT NOT NULL,
              pid INTEGER NOT NULL,
              ppid INTEGER,
              process_start_time REAL,
              parent_start_time REAL,
              user TEXT,
              task_name TEXT NOT NULL,
              process_name TEXT NOT NULL,
              exe TEXT,
              cmdline_hash TEXT,
              cmdline_text TEXT,
              first_seen_at REAL NOT NULL,
              last_seen_at REAL NOT NULL,
              duration_seconds REAL NOT NULL,
              status TEXT NOT NULL,
              sample_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS process_gpu_usages (
              session_id TEXT NOT NULL,
              node_id TEXT NOT NULL,
              gpu_uuid TEXT NOT NULL,
              first_seen_at REAL NOT NULL,
              last_seen_at REAL NOT NULL,
              max_memory_mb INTEGER NOT NULL,
              avg_memory_mb REAL NOT NULL,
              last_memory_mb INTEGER NOT NULL,
              sample_count INTEGER NOT NULL,
              PRIMARY KEY(session_id, gpu_uuid)
            );

            CREATE TABLE IF NOT EXISTS raw_snapshots (
              sampled_at REAL NOT NULL,
              node_id TEXT NOT NULL,
              payload_json TEXT NOT NULL
            );
            """
        )
        con.commit()

    def write_node_snapshot(self, snapshot: NodeSnapshot, *, write_raw: bool = False) -> None:
        con = self._con()
        sampled_at = snapshot.sampled_at
        written_sessions: set[str] = set()
        with con:
            con.execute(
                """
                INSERT INTO nodes (
                  node_id, hostname, display_name, first_seen_at, last_seen_at, agent_version, status
                )
                VALUES (?, ?, NULL, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                  hostname=excluded.hostname,
                  last_seen_at=excluded.last_seen_at,
                  agent_version=excluded.agent_version,
                  status=excluded.status
                """,
                (
                    snapshot.node_id,
                    snapshot.hostname,
                    sampled_at,
                    sampled_at,
                    snapshot.agent_version,
                    snapshot.status,
                ),
            )
            for gpu in snapshot.gpus:
                gpu_id = gpu.gpu_id or f"{snapshot.node_id}:{gpu.uuid}"
                con.execute(
                    """
                    INSERT INTO gpus (
                      gpu_id, node_id, uuid, gpu_index, pci_bus_id, name,
                      memory_total_mb, first_seen_at, last_seen_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(gpu_id) DO UPDATE SET
                      gpu_index=excluded.gpu_index,
                      pci_bus_id=excluded.pci_bus_id,
                      name=excluded.name,
                      memory_total_mb=excluded.memory_total_mb,
                      last_seen_at=excluded.last_seen_at
                    """,
                    (
                        gpu_id,
                        snapshot.node_id,
                        gpu.uuid,
                        gpu.index,
                        gpu.pci_bus_id,
                        gpu.name,
                        gpu.memory_total_mb,
                        sampled_at,
                        sampled_at,
                    ),
                )
                for process in gpu.processes:
                    self._write_process(
                        con,
                        snapshot,
                        gpu.uuid,
                        process,
                        written_sessions=written_sessions,
                    )

            if write_raw:
                con.execute(
                    """
                    INSERT INTO raw_snapshots(sampled_at, node_id, payload_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        sampled_at,
                        snapshot.node_id,
                        json.dumps(snapshot.to_dict(), ensure_ascii=False, separators=(",", ":")),
                    ),
                )

    def upsert_gpu_metric_rollups(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        con = self._con()
        with con:
            for row in rows:
                con.execute(
                    """
                    INSERT INTO gpu_metric_rollups (
                      bucket_start, bucket_seconds, node_id, gpu_uuid,
                      avg_gpu_utilization, max_gpu_utilization,
                      avg_memory_used_mb, max_memory_used_mb,
                      avg_power_watts, max_power_watts,
                      avg_temperature_c, max_temperature_c,
                      sample_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(bucket_start, bucket_seconds, node_id, gpu_uuid) DO UPDATE SET
                      avg_gpu_utilization=excluded.avg_gpu_utilization,
                      max_gpu_utilization=excluded.max_gpu_utilization,
                      avg_memory_used_mb=excluded.avg_memory_used_mb,
                      max_memory_used_mb=excluded.max_memory_used_mb,
                      avg_power_watts=excluded.avg_power_watts,
                      max_power_watts=excluded.max_power_watts,
                      avg_temperature_c=excluded.avg_temperature_c,
                      max_temperature_c=excluded.max_temperature_c,
                      sample_count=excluded.sample_count
                    """,
                    (
                        row["bucket_start"],
                        row["bucket_seconds"],
                        row["node_id"],
                        row["gpu_uuid"],
                        row["avg_gpu_utilization"],
                        row["max_gpu_utilization"],
                        row["avg_memory_used_mb"],
                        row["max_memory_used_mb"],
                        row["avg_power_watts"],
                        row["max_power_watts"],
                        row["avg_temperature_c"],
                        row["max_temperature_c"],
                        row["sample_count"],
                    ),
                )
        return len(rows)

    def close_stale_sessions(self, *, now: float, stale_after_seconds: float = 60.0) -> int:
        con = self._con()
        cutoff = now - stale_after_seconds
        with con:
            cursor = con.execute(
                """
                UPDATE process_sessions
                SET status='ended', duration_seconds=last_seen_at - first_seen_at
                WHERE status='running' AND last_seen_at < ?
                """,
                (cutoff,),
            )
        return cursor.rowcount

    def rollup_legacy_gpu_metric_samples(self, *, bucket_seconds: int = ROLLUP_20S) -> int:
        con = self._con()
        rows = con.execute(
            """
            SELECT
              CAST(sampled_at / ? AS INTEGER) * ? AS bucket_start,
              node_id,
              gpu_uuid,
              AVG(utilization_gpu) AS avg_gpu_utilization,
              MAX(utilization_gpu) AS max_gpu_utilization,
              AVG(memory_used_mb) AS avg_memory_used_mb,
              MAX(memory_used_mb) AS max_memory_used_mb,
              AVG(power_watts) AS avg_power_watts,
              MAX(power_watts) AS max_power_watts,
              AVG(temperature_c) AS avg_temperature_c,
              MAX(temperature_c) AS max_temperature_c,
              COUNT(*) AS sample_count
            FROM gpu_metric_samples
            GROUP BY bucket_start, node_id, gpu_uuid
            """,
            (bucket_seconds, bucket_seconds),
        ).fetchall()
        return self.upsert_gpu_metric_rollups(
            [_rollup_row_from_sql(row, bucket_seconds=bucket_seconds) for row in rows]
        )

    def rollup_gpu_metrics(self, *, bucket_seconds: int) -> int:
        return self.rollup_legacy_gpu_metric_samples(bucket_seconds=bucket_seconds)

    def rollup_gpu_metric_rollups(
        self,
        *,
        from_bucket_seconds: int,
        to_bucket_seconds: int,
        now: float | None = None,
    ) -> int:
        if ROLLUP_SOURCE_BUCKETS.get(to_bucket_seconds) != from_bucket_seconds:
            raise ValueError(
                f"unsupported rollup path: {from_bucket_seconds} -> {to_bucket_seconds}"
            )
        current_time = time.time() if now is None else now
        cutoff = current_time - to_bucket_seconds
        con = self._con()
        rows = con.execute(
            """
            SELECT
              target_bucket_start AS bucket_start,
              node_id,
              gpu_uuid,
              SUM(avg_gpu_utilization * sample_count) / SUM(sample_count)
                AS avg_gpu_utilization,
              MAX(max_gpu_utilization) AS max_gpu_utilization,
              SUM(avg_memory_used_mb * sample_count) / SUM(sample_count)
                AS avg_memory_used_mb,
              MAX(max_memory_used_mb) AS max_memory_used_mb,
              SUM(avg_power_watts * sample_count) / SUM(sample_count)
                AS avg_power_watts,
              MAX(max_power_watts) AS max_power_watts,
              SUM(avg_temperature_c * sample_count) / SUM(sample_count)
                AS avg_temperature_c,
              MAX(max_temperature_c) AS max_temperature_c,
              SUM(sample_count) AS sample_count
            FROM (
              SELECT
                CAST(bucket_start / ? AS INTEGER) * ? AS target_bucket_start,
                node_id,
                gpu_uuid,
                avg_gpu_utilization,
                max_gpu_utilization,
                avg_memory_used_mb,
                max_memory_used_mb,
                avg_power_watts,
                max_power_watts,
                avg_temperature_c,
                max_temperature_c,
                sample_count
              FROM gpu_metric_rollups
              WHERE bucket_seconds = ?
            )
            GROUP BY target_bucket_start, node_id, gpu_uuid
            HAVING target_bucket_start + ? <= ? AND sample_count > 0
            """,
            (
                to_bucket_seconds,
                to_bucket_seconds,
                from_bucket_seconds,
                to_bucket_seconds,
                cutoff,
            ),
        ).fetchall()
        return self.upsert_gpu_metric_rollups(
            [_rollup_row_from_sql(row, bucket_seconds=to_bucket_seconds) for row in rows]
        )

    def prune_rollups(
        self,
        *,
        now: float | None = None,
        bucket_seconds: int | None = None,
    ) -> int:
        current_time = time.time() if now is None else now
        buckets = [bucket_seconds] if bucket_seconds is not None else list(ROLLUP_RETENTION_SECONDS)
        deleted = 0
        con = self._con()
        with con:
            for bucket in buckets:
                retention = ROLLUP_RETENTION_SECONDS[bucket]
                cutoff = current_time - retention
                cursor = con.execute(
                    """
                    DELETE FROM gpu_metric_rollups
                    WHERE bucket_seconds = ? AND bucket_start < ?
                    """,
                    (bucket, cutoff),
                )
                deleted += cursor.rowcount
        return deleted

    def maintain(
        self,
        *,
        now: float | None = None,
        stale_session_seconds: float = 300.0,
        raw_retention_seconds: float = RAW_SNAPSHOT_RETENTION_SECONDS,
    ) -> dict[str, int]:
        current_time = time.time() if now is None else now
        return {
            "closed_sessions": self.close_stale_sessions(
                now=current_time,
                stale_after_seconds=stale_session_seconds,
            ),
            "rollups_2m": self.rollup_gpu_metric_rollups(
                from_bucket_seconds=ROLLUP_20S,
                to_bucket_seconds=ROLLUP_2M,
                now=current_time,
            ),
            "rollups_1h": self.rollup_gpu_metric_rollups(
                from_bucket_seconds=ROLLUP_2M,
                to_bucket_seconds=ROLLUP_1H,
                now=current_time,
            ),
            "pruned_rollups": self.prune_rollups(now=current_time),
            "pruned_raw_snapshots": self.prune_raw_snapshots(
                now=current_time,
                retention_seconds=raw_retention_seconds,
            ),
        }

    def prune_raw_snapshots(
        self,
        *,
        now: float | None = None,
        retention_seconds: float = RAW_SNAPSHOT_RETENTION_SECONDS,
    ) -> int:
        con = self._con()
        cutoff = (time.time() if now is None else now) - retention_seconds
        with con:
            cursor = con.execute("DELETE FROM raw_snapshots WHERE sampled_at < ?", (cutoff,))
        return cursor.rowcount

    def query_gpu_history(
        self,
        *,
        node_id: str | None = None,
        gpu_uuid: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        bucket_seconds = _select_history_bucket(since=since, until=until)
        where, params = _rollup_filters(
            node_id=node_id,
            gpu_uuid=gpu_uuid,
            since=since,
            until=until,
            bucket_seconds=bucket_seconds,
        )
        params.append(limit)
        rows = self._con().execute(
            f"""
            SELECT bucket_start, bucket_seconds, node_id, gpu_uuid,
                   avg_gpu_utilization, max_gpu_utilization,
                   avg_memory_used_mb, max_memory_used_mb,
                   avg_power_watts, max_power_watts,
                   avg_temperature_c, max_temperature_c,
                   sample_count
            FROM gpu_metric_rollups
            {where}
            ORDER BY bucket_start ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_history_row_from_rollup(row) for row in rows]

    def query_tasks(
        self,
        *,
        user: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if user:
            clauses.append("user = ?")
            params.append(user)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self._con().execute(
            f"""
            SELECT session_id, node_id, pid, process_start_time, user, task_name,
                   ppid, parent_start_time, process_name, exe, cmdline_hash,
                   first_seen_at, last_seen_at, duration_seconds, status, sample_count
            FROM process_sessions
            {where}
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def query_users(self) -> list[dict[str, Any]]:
        rows = self._con().execute(
            """
            SELECT user, COUNT(*) AS task_count, SUM(duration_seconds) AS total_duration_seconds,
                   MAX(last_seen_at) AS last_seen_at
            FROM process_sessions
            WHERE user IS NOT NULL
            GROUP BY user
            ORDER BY last_seen_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def _write_process(
        self,
        con: sqlite3.Connection,
        snapshot: NodeSnapshot,
        gpu_uuid: str,
        process: GpuProcess,
        written_sessions: set[str],
    ) -> None:
        sampled_at = snapshot.sampled_at
        session_id = process_session_id(snapshot.node_id, process)
        task_name = process.task_name or process.name or f"unknown:{process.pid}"
        if session_id not in written_sessions:
            con.execute(
                """
                INSERT INTO process_sessions (
                  session_id, node_id, pid, ppid, process_start_time, parent_start_time,
                  user, task_name, process_name, exe, cmdline_hash, cmdline_text,
                  first_seen_at, last_seen_at, duration_seconds, status, sample_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 'running', 1)
                ON CONFLICT(session_id) DO UPDATE SET
                  ppid=COALESCE(excluded.ppid, process_sessions.ppid),
                  parent_start_time=COALESCE(
                    excluded.parent_start_time,
                    process_sessions.parent_start_time
                  ),
                  user=COALESCE(excluded.user, process_sessions.user),
                  task_name=excluded.task_name,
                  process_name=excluded.process_name,
                  exe=COALESCE(excluded.exe, process_sessions.exe),
                  cmdline_hash=COALESCE(excluded.cmdline_hash, process_sessions.cmdline_hash),
                  cmdline_text=COALESCE(excluded.cmdline_text, process_sessions.cmdline_text),
                  last_seen_at=excluded.last_seen_at,
                  duration_seconds=excluded.last_seen_at - process_sessions.first_seen_at,
                  status='running',
                  sample_count=process_sessions.sample_count + 1
                """,
                (
                    session_id,
                    snapshot.node_id,
                    process.pid,
                    process.ppid,
                    process.process_start_time,
                    process.parent_start_time,
                    process.user,
                    task_name,
                    process.name,
                    process.exe,
                    process.cmdline_hash,
                    process.cmdline,
                    sampled_at,
                    sampled_at,
                ),
            )
            written_sessions.add(session_id)
        con.execute(
            """
            INSERT INTO process_gpu_usages (
              session_id, node_id, gpu_uuid, first_seen_at, last_seen_at,
              max_memory_mb, avg_memory_mb, last_memory_mb, sample_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(session_id, gpu_uuid) DO UPDATE SET
              last_seen_at=excluded.last_seen_at,
              max_memory_mb=MAX(process_gpu_usages.max_memory_mb, excluded.last_memory_mb),
              avg_memory_mb=(
                (process_gpu_usages.avg_memory_mb * process_gpu_usages.sample_count)
                + excluded.last_memory_mb
              ) / (process_gpu_usages.sample_count + 1),
              last_memory_mb=excluded.last_memory_mb,
              sample_count=process_gpu_usages.sample_count + 1
            """,
            (
                session_id,
                snapshot.node_id,
                gpu_uuid,
                sampled_at,
                sampled_at,
                process.gpu_memory_mb,
                float(process.gpu_memory_mb),
                process.gpu_memory_mb,
            ),
        )

    def _con(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("SQLiteStore is not open")
        return self.connection


class AsyncDBSink:
    def __init__(self, config: SQLiteSinkConfig):
        self.config = config
        self.store = SQLiteStore(config.path)
        self.queue: asyncio.Queue[tuple[NodeSnapshot, bool]] = asyncio.Queue(
            maxsize=config.queue_size
        )
        self._task: asyncio.Task[None] | None = None
        self._last_raw_at = 0.0
        self._last_20s_flush_at = 0.0
        self._last_2m_rollup_at = 0.0
        self._last_1h_rollup_at = 0.0
        self._last_prune_at = 0.0
        self._rollup_20s: dict[tuple[float, str, str], RollupBucket] = {}
        self.dropped_samples = 0
        self.write_errors = 0

    async def start(self) -> None:
        if self.store.connection is None:
            self.store.open()
        self._task = asyncio.create_task(self._worker(), name="constella-db-writer")

    async def stop(self) -> None:
        if self._task:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.queue.join(), timeout=5.0)
            self.flush_rollups(now=time.time())
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.store.close()

    def submit_node_snapshot(self, snapshot: NodeSnapshot) -> bool:
        write_raw = False
        if self.config.raw_snapshot_interval > 0:
            if snapshot.sampled_at - self._last_raw_at >= self.config.raw_snapshot_interval:
                write_raw = True
                self._last_raw_at = snapshot.sampled_at
        try:
            self.queue.put_nowait((snapshot, write_raw))
            return True
        except asyncio.QueueFull:
            self.dropped_samples += 1
            return False

    def flush_rollups(self, *, now: float) -> int:
        ready: list[dict[str, Any]] = []
        for key, bucket in list(self._rollup_20s.items()):
            if bucket.bucket_start + ROLLUP_20S <= now - ROLLUP_20S:
                ready.append(bucket.to_row(ROLLUP_20S))
                del self._rollup_20s[key]
        return self.store.upsert_gpu_metric_rollups(ready)

    async def _worker(self) -> None:
        while True:
            try:
                snapshot, write_raw = await asyncio.wait_for(self.queue.get(), timeout=10.0)
            except asyncio.TimeoutError:
                try:
                    self._run_scheduled_maintenance(now=time.time())
                except Exception:
                    self.write_errors += 1
                continue
            try:
                self.store.write_node_snapshot(snapshot, write_raw=write_raw)
                self._accumulate_snapshot(snapshot)
                self._run_scheduled_maintenance(now=max(time.time(), snapshot.sampled_at))
            except Exception:
                self.write_errors += 1
            finally:
                self.queue.task_done()

    def _accumulate_snapshot(self, snapshot: NodeSnapshot) -> None:
        bucket_start = float(int(snapshot.sampled_at // ROLLUP_20S) * ROLLUP_20S)
        for gpu in snapshot.gpus:
            key = (bucket_start, snapshot.node_id, gpu.uuid)
            bucket = self._rollup_20s.get(key)
            if bucket is None:
                bucket = RollupBucket(
                    bucket_start=bucket_start,
                    node_id=snapshot.node_id,
                    gpu_uuid=gpu.uuid,
                )
                self._rollup_20s[key] = bucket
            bucket.add_gpu(gpu)

    def _run_scheduled_maintenance(self, *, now: float) -> None:
        if now - self._last_20s_flush_at >= 10.0:
            self.flush_rollups(now=now)
            self._last_20s_flush_at = now
        if now - self._last_2m_rollup_at >= 120.0:
            self.store.rollup_gpu_metric_rollups(
                from_bucket_seconds=ROLLUP_20S,
                to_bucket_seconds=ROLLUP_2M,
                now=now,
            )
            self._last_2m_rollup_at = now
        if now - self._last_1h_rollup_at >= 3600.0:
            self.store.rollup_gpu_metric_rollups(
                from_bucket_seconds=ROLLUP_2M,
                to_bucket_seconds=ROLLUP_1H,
                now=now,
            )
            self._last_1h_rollup_at = now
        if now - self._last_prune_at >= 600.0:
            self.store.prune_rollups(now=now)
            self.store.prune_raw_snapshots(now=now)
            self._last_prune_at = now


def _rollup_row_from_sql(row: sqlite3.Row, *, bucket_seconds: int) -> dict[str, Any]:
    return {
        "bucket_start": row["bucket_start"],
        "bucket_seconds": bucket_seconds,
        "node_id": row["node_id"],
        "gpu_uuid": row["gpu_uuid"],
        "avg_gpu_utilization": row["avg_gpu_utilization"],
        "max_gpu_utilization": row["max_gpu_utilization"],
        "avg_memory_used_mb": row["avg_memory_used_mb"],
        "max_memory_used_mb": row["max_memory_used_mb"],
        "avg_power_watts": row["avg_power_watts"],
        "max_power_watts": row["max_power_watts"],
        "avg_temperature_c": row["avg_temperature_c"],
        "max_temperature_c": row["max_temperature_c"],
        "sample_count": row["sample_count"],
    }


def _select_history_bucket(*, since: float | None, until: float | None) -> int:
    if since is None:
        return ROLLUP_20S
    end = time.time() if until is None else until
    span = max(0.0, end - since)
    if span <= ROLLUP_RETENTION_SECONDS[ROLLUP_20S]:
        return ROLLUP_20S
    if span <= ROLLUP_RETENTION_SECONDS[ROLLUP_2M]:
        return ROLLUP_2M
    return ROLLUP_1H


def _history_row_from_rollup(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "sampled_at": row["bucket_start"],
        "bucket_start": row["bucket_start"],
        "bucket_seconds": row["bucket_seconds"],
        "node_id": row["node_id"],
        "gpu_uuid": row["gpu_uuid"],
        "utilization_gpu": row["avg_gpu_utilization"],
        "memory_used_mb": row["avg_memory_used_mb"],
        "power_watts": row["avg_power_watts"],
        "temperature_c": row["avg_temperature_c"],
        "avg_gpu_utilization": row["avg_gpu_utilization"],
        "max_gpu_utilization": row["max_gpu_utilization"],
        "avg_memory_used_mb": row["avg_memory_used_mb"],
        "max_memory_used_mb": row["max_memory_used_mb"],
        "avg_power_watts": row["avg_power_watts"],
        "max_power_watts": row["max_power_watts"],
        "avg_temperature_c": row["avg_temperature_c"],
        "max_temperature_c": row["max_temperature_c"],
        "sample_count": row["sample_count"],
    }


def _rollup_filters(
    *,
    node_id: str | None,
    gpu_uuid: str | None,
    since: float | None,
    until: float | None,
    bucket_seconds: int,
) -> tuple[str, list[Any]]:
    clauses: list[str] = ["bucket_seconds = ?"]
    params: list[Any] = [bucket_seconds]
    if node_id:
        clauses.append("node_id = ?")
        params.append(node_id)
    if gpu_uuid:
        clauses.append("gpu_uuid = ?")
        params.append(gpu_uuid)
    if since is not None:
        clauses.append("bucket_start >= ?")
        params.append(since)
    if until is not None:
        clauses.append("bucket_start <= ?")
        params.append(until)
    return "WHERE " + " AND ".join(clauses), params
