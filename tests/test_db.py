from __future__ import annotations

import asyncio
import logging
import sqlite3

from fastapi.testclient import TestClient

from constella.app import create_app
from constella.cluster import ClusterState
from constella.db import AsyncDBSink, ROLLUP_20S, ROLLUP_2M, SQLiteSinkConfig, SQLiteStore
from constella.performance_rollup import NvidiaGpmRollupBucket
from constella.schema import (
    AcceleratorPerformance,
    GpuInfo,
    GpuProcess,
    NodeSnapshot,
    node_totals_from_gpus,
)


def test_store_migrates_existing_gpu_inventory_columns(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE gpus (
          gpu_id TEXT PRIMARY KEY, node_id TEXT NOT NULL, uuid TEXT NOT NULL,
          gpu_index INTEGER NOT NULL, pci_bus_id TEXT, name TEXT NOT NULL,
          memory_total_mb INTEGER NOT NULL, first_seen_at REAL NOT NULL,
          last_seen_at REAL NOT NULL
        )
        """
    )
    connection.close()

    store = SQLiteStore(path)
    store.open()
    try:
        columns = {row[1] for row in store.connection.execute("PRAGMA table_info(gpus)")}
        assert {"device_type", "card_id", "die_id"} <= columns
    finally:
        store.close()


def test_store_migrates_interconnect_columns_into_existing_gpm_table(tmp_path) -> None:
    path = tmp_path / "legacy-gpm.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE nvidia_gpm_rollups (
          bucket_start REAL NOT NULL, bucket_seconds INTEGER NOT NULL,
          node_id TEXT NOT NULL, gpu_uuid TEXT NOT NULL, expected_count INTEGER NOT NULL,
          avg_sm_active REAL, max_sm_active REAL, sm_active_count INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(bucket_start, bucket_seconds, node_id, gpu_uuid)
        )
        """
    )
    connection.close()

    store = SQLiteStore(path)
    store.open()
    try:
        columns = {
            row[1]
            for row in store.connection.execute("PRAGMA table_info(nvidia_gpm_rollups)")
        }
        assert {
            "avg_pcie_tx_per_second",
            "pcie_rx_per_second_count",
            "avg_nvlink_tx_per_second",
            "nvlink_rx_per_second_count",
        } <= columns
    finally:
        store.close()


def make_node_snapshot(sampled_at: float, *, gpu_util: int = 50) -> NodeSnapshot:
    process = GpuProcess(
        pid=1234,
        name="python",
        task_name="train.py",
        user="alice",
        cmdline="python train.py",
        cmdline_hash="hash",
        gpu_memory_mb=2048,
        ppid=4321,
        process_start_time=90.0,
        parent_start_time=80.0,
    )
    gpus = [
        GpuInfo(
            index=0,
            node_id="node-a",
            gpu_id="node-a:GPU-0",
            uuid="GPU-0",
            name="NVIDIA Test",
            utilization_gpu=gpu_util,
            utilization_mem=20,
            memory_total_mb=100,
            memory_used_mb=20,
            power_watts=100,
            power_limit_watts=200,
            temperature_c=40,
            processes=[process],
        ),
        GpuInfo(
            index=1,
            node_id="node-a",
            gpu_id="node-a:GPU-1",
            uuid="GPU-1",
            name="NVIDIA Test",
            utilization_gpu=gpu_util + 10,
            utilization_mem=30,
            memory_total_mb=100,
            memory_used_mb=30,
            power_watts=120,
            power_limit_watts=200,
            temperature_c=45,
            processes=[process],
        ),
    ]
    return NodeSnapshot(
        node_id="node-a",
        hostname="node-a-host",
        seq=int(sampled_at),
        sampled_at=sampled_at,
        received_at=sampled_at + 0.1,
        refresh_interval=1.0,
        process_interval=5.0,
        status="online",
        source="test",
        gpus=gpus,
        totals=node_totals_from_gpus(gpus),
        agent_version="0.1.1",
    )


def add_gpm(
    snapshot: NodeSnapshot,
    *,
    sm_active: float | None,
    status: str = "available",
) -> NodeSnapshot:
    snapshot.gpus[0].performance = AcceleratorPerformance(
        profile="nvidia.gpm.v1",
        status=status,
        sampled_at=snapshot.sampled_at,
        interval_ms=1000.0,
        metrics={"nvidia.gpm.sm_active": sm_active} if sm_active is not None else {},
    )
    return snapshot


def test_sqlite_store_writes_sessions_and_multi_gpu_usage(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "constella.db")
    store.open()
    try:
        store.write_node_snapshot(make_node_snapshot(100.0))

        con = store.connection
        assert con is not None
        assert con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM gpus").fetchone()[0] == 2
        columns = {row[1] for row in con.execute("PRAGMA table_info(gpus)")}
        assert {"device_type", "card_id", "die_id"} <= columns
        assert con.execute("SELECT COUNT(*) FROM gpu_metric_samples").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM process_sessions").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM process_gpu_usages").fetchone()[0] == 2
        session = con.execute(
            "SELECT task_name, ppid, parent_start_time, sample_count FROM process_sessions"
        ).fetchone()
        assert dict(session) == {
            "task_name": "train.py",
            "ppid": 4321,
            "parent_start_time": 80.0,
            "sample_count": 1,
        }
    finally:
        store.close()


def test_sqlite_sink_flushes_20s_rollup_and_raw_retention(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    try:
        sink.store.write_node_snapshot(make_node_snapshot(100.0, gpu_util=20), write_raw=True)
        sink._accumulate_snapshot(make_node_snapshot(100.0, gpu_util=20))
        sink.store.write_node_snapshot(make_node_snapshot(105.0, gpu_util=40), write_raw=True)
        sink._accumulate_snapshot(make_node_snapshot(105.0, gpu_util=40))

        assert sink.flush_rollups(now=140.0) == 2
        con = sink.store.connection
        assert con is not None
        assert con.execute("SELECT COUNT(*) FROM gpu_metric_samples").fetchone()[0] == 0
        rollup = con.execute(
            """
            SELECT avg_gpu_utilization, max_gpu_utilization, sample_count
            FROM gpu_metric_rollups
            WHERE node_id='node-a' AND gpu_uuid='GPU-0' AND bucket_seconds=20
            """
        ).fetchone()
        assert round(rollup["avg_gpu_utilization"], 1) == 30.0
        assert rollup["max_gpu_utilization"] == 40.0
        assert rollup["sample_count"] == 2

        assert sink.store.prune_raw_snapshots(now=200.0, retention_seconds=50.0) == 2
    finally:
        sink.store.close()


def test_sqlite_sink_writes_gpm_rollup_with_metric_coverage(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    try:
        sink._accumulate_snapshot(add_gpm(make_node_snapshot(100.0), sm_active=20.0))
        sink._accumulate_snapshot(add_gpm(make_node_snapshot(105.0), sm_active=40.0))
        sink._accumulate_snapshot(
            add_gpm(make_node_snapshot(110.0), sm_active=None, status="error")
        )

        assert sink.flush_rollups(now=140.0) == 2
        points = sink.store.query_nvidia_gpm_history(
            node_id="node-a",
            gpu_uuid="GPU-0",
            since=100.0,
            until=119.0,
            bucket_seconds=ROLLUP_20S,
            metrics=["nvidia.gpm.sm_active"],
        )

        assert len(points) == 1
        assert points[0]["expected_count"] == 3
        assert points[0]["metrics"]["nvidia.gpm.sm_active"] == {
            "avg": 30.0,
            "max": 40.0,
            "valid_count": 2,
            "coverage": 66.67,
        }
    finally:
        sink.store.close()


def test_sqlite_sink_does_not_create_gpm_rollups_for_unsupported_or_npu(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    try:
        unsupported = add_gpm(
            make_node_snapshot(100.0), sm_active=None, status="unsupported"
        )
        npu = make_node_snapshot(105.0)
        for gpu in npu.gpus:
            gpu.device_type = "ascend"
        sink._accumulate_snapshot(unsupported)
        sink._accumulate_snapshot(npu)
        sink.flush_rollups(now=140.0)

        count = sink.store.connection.execute(
            "SELECT COUNT(*) FROM nvidia_gpm_rollups"
        ).fetchone()[0]
        assert count == 0
    finally:
        sink.store.close()


def test_sqlite_sink_keeps_rollups_when_flush_fails(tmp_path, monkeypatch) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    sink._accumulate_snapshot(make_node_snapshot(100.0, gpu_util=20))
    original_upsert = sink.store.upsert_gpu_metric_rollups

    def fail_upsert(rows) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(sink.store, "upsert_gpu_metric_rollups", fail_upsert)
    try:
        try:
            sink.flush_rollups(now=140.0)
        except OSError as exc:
            assert str(exc) == "disk full"
        else:
            raise AssertionError("flush should fail")

        assert len(sink._rollup_20s) == 2

        monkeypatch.setattr(sink.store, "upsert_gpu_metric_rollups", original_upsert)
        assert sink.flush_rollups(now=140.0) == 2
        assert sink._rollup_20s == {}
    finally:
        sink.store.close()


def test_sqlite_worker_logs_errors_reports_health_and_recovers(
    tmp_path, monkeypatch, caplog
) -> None:
    async def exercise() -> None:
        sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
        original_write = sink.store.write_node_snapshot

        def fail_write(snapshot, *, write_raw=False) -> None:
            raise OSError("disk full")

        await sink.start()
        monkeypatch.setattr(sink.store, "write_node_snapshot", fail_write)
        sink.submit_node_snapshot(make_node_snapshot(100.0))
        await sink.queue.join()

        failed = sink.status()
        assert failed["healthy"] is False
        assert failed["write_errors"] == 1
        assert failed["consecutive_errors"] == 1
        assert failed["last_error_operation"] == "write_node_snapshot"
        assert failed["last_error"] == "OSError: disk full"
        assert failed["pending_rollup_buckets"] == 2
        assert "SQLite sink operation failed" in caplog.text

        monkeypatch.setattr(sink.store, "write_node_snapshot", original_write)
        sink.submit_node_snapshot(make_node_snapshot(105.0))
        await sink.queue.join()

        recovered = sink.status()
        assert recovered["healthy"] is True
        assert recovered["consecutive_errors"] == 0
        assert recovered["last_success_at"] is not None
        await sink.stop()

    with caplog.at_level(logging.INFO, logger="constella.db"):
        asyncio.run(exercise())


def test_sqlite_sink_closes_stale_sessions_during_scheduled_maintenance(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    try:
        sink.store.write_node_snapshot(make_node_snapshot(100.0))

        sink._run_scheduled_maintenance(now=2000.0)

        session = sink.store.connection.execute(
            "SELECT status, duration_seconds FROM process_sessions"
        ).fetchone()
        assert dict(session) == {"status": "ended", "duration_seconds": 0.0}
    finally:
        sink.store.close()


def test_sqlite_store_rollup_uses_sample_count_weighting(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "constella.db")
    store.open()
    try:
        store.upsert_gpu_metric_rollups(
            [
                {
                    "bucket_start": 0.0,
                    "bucket_seconds": ROLLUP_20S,
                    "node_id": "node-a",
                    "gpu_uuid": "GPU-0",
                    "avg_gpu_utilization": 20.0,
                    "max_gpu_utilization": 25.0,
                    "avg_memory_used_mb": 10.0,
                    "max_memory_used_mb": 12,
                    "avg_power_watts": 100.0,
                    "max_power_watts": 110.0,
                    "avg_temperature_c": 40.0,
                    "max_temperature_c": 42,
                    "sample_count": 1,
                },
                {
                    "bucket_start": 20.0,
                    "bucket_seconds": ROLLUP_20S,
                    "node_id": "node-a",
                    "gpu_uuid": "GPU-0",
                    "avg_gpu_utilization": 80.0,
                    "max_gpu_utilization": 90.0,
                    "avg_memory_used_mb": 30.0,
                    "max_memory_used_mb": 40,
                    "avg_power_watts": 200.0,
                    "max_power_watts": 250.0,
                    "avg_temperature_c": 60.0,
                    "max_temperature_c": 70,
                    "sample_count": 3,
                },
            ]
        )

        assert (
            store.rollup_gpu_metric_rollups(
                from_bucket_seconds=ROLLUP_20S,
                to_bucket_seconds=ROLLUP_2M,
                now=400.0,
            )
            == 1
        )
        rollup = store.connection.execute(
            """
            SELECT avg_gpu_utilization, max_gpu_utilization, avg_memory_used_mb,
                   max_memory_used_mb, sample_count
            FROM gpu_metric_rollups
            WHERE bucket_seconds=120 AND node_id='node-a' AND gpu_uuid='GPU-0'
            """
        ).fetchone()
        assert round(rollup["avg_gpu_utilization"], 1) == 65.0
        assert rollup["max_gpu_utilization"] == 90.0
        assert round(rollup["avg_memory_used_mb"], 1) == 25.0
        assert rollup["max_memory_used_mb"] == 40
        assert rollup["sample_count"] == 4
    finally:
        store.close()


def test_sqlite_store_gpm_rollup_weights_each_metric_by_valid_count(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "constella.db")
    store.open()
    try:
        first = NvidiaGpmRollupBucket(0.0, "node-a", "GPU-0")
        first.add(
            AcceleratorPerformance(
                profile="nvidia.gpm.v1",
                status="available",
                sampled_at=0.0,
                metrics={"nvidia.gpm.sm_active": 20.0},
            )
        )
        second = NvidiaGpmRollupBucket(20.0, "node-a", "GPU-0")
        for value in (70.0, 80.0, 90.0):
            second.add(
                AcceleratorPerformance(
                    profile="nvidia.gpm.v1",
                    status="available",
                    sampled_at=20.0,
                    metrics={"nvidia.gpm.sm_active": value},
                )
            )
        store.upsert_nvidia_gpm_rollups(
            [first.to_row(ROLLUP_20S), second.to_row(ROLLUP_20S)]
        )

        assert (
            store.rollup_nvidia_gpm_rollups(
                from_bucket_seconds=ROLLUP_20S,
                to_bucket_seconds=ROLLUP_2M,
                now=400.0,
            )
            == 1
        )
        point = store.query_nvidia_gpm_history(
            node_id="node-a",
            gpu_uuid="GPU-0",
            since=0.0,
            until=119.0,
            bucket_seconds=ROLLUP_2M,
            metrics=["nvidia.gpm.sm_active"],
        )[0]

        assert point["expected_count"] == 4
        assert point["metrics"]["nvidia.gpm.sm_active"] == {
            "avg": 65.0,
            "max": 90.0,
            "valid_count": 4,
            "coverage": 100.0,
        }
    finally:
        store.close()


def test_sqlite_store_round_trips_interconnect_rollups(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "constella.db")
    store.open()
    try:
        bucket = NvidiaGpmRollupBucket(0.0, "node-a", "GPU-0")
        bucket.add(
            AcceleratorPerformance(
                profile="nvidia.gpm.v1",
                status="available",
                sampled_at=0.0,
                metrics={
                    "nvidia.gpm.pcie_tx_per_second": 512.0,
                    "nvidia.gpm.pcie_rx_per_second": 1024.0,
                    "nvidia.gpm.nvlink_tx_per_second": 2048.0,
                    "nvidia.gpm.nvlink_rx_per_second": 4096.0,
                },
            )
        )
        store.upsert_nvidia_gpm_rollups([bucket.to_row(ROLLUP_20S)])

        point = store.query_nvidia_gpm_history(
            node_id="node-a",
            gpu_uuid="GPU-0",
            bucket_seconds=ROLLUP_20S,
            metrics=[
                "nvidia.gpm.pcie_tx_per_second",
                "nvidia.gpm.nvlink_rx_per_second",
            ],
        )[0]

        assert point["metrics"]["nvidia.gpm.pcie_tx_per_second"]["avg"] == 512.0
        assert point["metrics"]["nvidia.gpm.nvlink_rx_per_second"]["avg"] == 4096.0
    finally:
        store.close()


def test_db_history_api_returns_disabled_without_sink() -> None:
    client = TestClient(create_app(cluster_state=ClusterState(local_node_id="local")))

    response = client.get("/api/history/gpu")

    assert response.status_code == 200
    assert response.json() == {"enabled": False, "items": []}


def test_health_api_exposes_database_status(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    client = TestClient(
        create_app(cluster_state=ClusterState(local_node_id="local"), db_sink=sink)
    )

    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["database"]["enabled"] is True
    assert payload["database"]["healthy"] is True
    assert payload["database"]["queue_depth"] == 0
    assert payload["database"]["write_errors"] == 0
    sink.store.close()


def test_db_history_api_reads_sink(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    sink.store.write_node_snapshot(make_node_snapshot(100.0))
    client = TestClient(
        create_app(
            cluster_state=ClusterState(local_node_id="local"),
            db_sink=sink,
        )
    )

    response = client.get("/api/history/tasks?user=alice")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["items"][0]["task_name"] == "train.py"
    sink.store.close()


def test_gpu_history_api_reads_rollups(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    sink.store.upsert_gpu_metric_rollups(
        [
            {
                "bucket_start": 100.0,
                "bucket_seconds": ROLLUP_20S,
                "node_id": "node-a",
                "gpu_uuid": "GPU-0",
                "avg_gpu_utilization": 42.0,
                "max_gpu_utilization": 50.0,
                "avg_memory_used_mb": 2048.0,
                "max_memory_used_mb": 4096,
                "avg_power_watts": 125.0,
                "max_power_watts": 140.0,
                "avg_temperature_c": 44.0,
                "max_temperature_c": 46,
                "sample_count": 2,
            }
        ]
    )
    client = TestClient(
        create_app(
            cluster_state=ClusterState(local_node_id="local"),
            db_sink=sink,
        )
    )

    response = client.get("/api/history/gpu?node_id=node-a&gpu_uuid=GPU-0&since=90&until=130")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["items"] == [
        {
            "sampled_at": 100.0,
            "bucket_start": 100.0,
            "bucket_seconds": 20,
            "node_id": "node-a",
            "gpu_uuid": "GPU-0",
            "utilization_gpu": 42.0,
            "memory_used_mb": 2048.0,
            "power_watts": 125.0,
            "temperature_c": 44.0,
            "avg_gpu_utilization": 42.0,
            "max_gpu_utilization": 50.0,
            "avg_memory_used_mb": 2048.0,
            "max_memory_used_mb": 4096,
            "avg_power_watts": 125.0,
            "max_power_watts": 140.0,
            "avg_temperature_c": 44.0,
            "max_temperature_c": 46,
            "sample_count": 2,
        }
    ]
    sink.store.close()
